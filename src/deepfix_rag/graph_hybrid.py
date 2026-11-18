"""The `graph_hybrid` retriever: entity-anchored graph retrieval merged with dense.

The graph half of the LlamaIndex property-graph guide is a retriever that first
finds the entities a question names and then runs a ``VectorContextRetriever`` for
each of them, instead of embedding the whole question into one blurred average.
``CustomPGRetriever`` is the documented extension point for exactly that, so this
module subclasses it rather than reimplementing the graph-store plumbing.

The result has to stay citable. A ``VectorContextRetriever`` returns triplets
(`subject -> relation -> object` text), which carry no ``doc_id`` and cannot
ground an answer, so the triplet's source chunk is loaded from the graph store and
returned instead, keeping its own metadata. That is what lets Ask, the vote path,
and the grounding status work unchanged for a ``graph_hybrid`` champion.
"""

from __future__ import annotations

from typing import Any, Sequence

from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.bridge.pydantic import BaseModel, Field
from llama_index.core.graph_stores.types import PropertyGraphStore
from llama_index.core.indices.property_graph.sub_retrievers.base import BasePGRetriever
from llama_index.core.indices.property_graph.sub_retrievers.custom import CustomPGRetriever
from llama_index.core.indices.property_graph.sub_retrievers.vector import (
    VectorContextRetriever,
)
from llama_index.core.llms.llm import LLM
from llama_index.core.prompts import PromptTemplate
from llama_index.core.schema import BaseNode, NodeWithScore, QueryBundle
from llama_index.core.vector_stores.types import BasePydanticVectorStore

from deepfix_rag.config.settings import LOGGER

ENTITY_PROMPT = PromptTemplate(
    "Extract the named entities from the following question. Include people, "
    "parliamentary groups, ministries, committees, printed papers such as "
    "'BT-Drucksache 20/123', laws, legislative procedures, and policy topics. "
    "Use the name as the question writes it and do not invent entities, do not "
    "explain, and return at most {limit} names.\n"
    "Question: {text}\n"
)


class QueryEntities(BaseModel):
    """The entity names a question mentions."""

    names: list[str] = Field(default_factory=list, description="Entity names in the question.")


class EntityGraphRetriever(CustomPGRetriever):
    """Retrieve the graph neighbourhood of each entity a question names.

    ``init`` receives the constructor keyword arguments, so the pipeline passes the
    LLM, the embedding model, and the traversal depth the same way it passes them
    to every other retriever.
    """

    def init(
        self,
        llm: LLM | None = None,
        embed_model: BaseEmbedding | None = None,
        similarity_top_k: int = 4,
        path_depth: int = 1,
        entity_limit: int = 5,
        **kwargs: Any,
    ) -> None:
        self.llm = llm
        self.embed_model = embed_model
        self.entity_limit = entity_limit
        # include_text stays off: the triplet text is not what an answer cites, so
        # the chunk each triplet came from is loaded below instead.
        self.vector_retriever = VectorContextRetriever(
            graph_store=self.graph_store,
            include_text=False,
            embed_model=embed_model,
            similarity_top_k=similarity_top_k,
            path_depth=path_depth,
        )

    def detect_entities(self, query_str: str) -> list[str]:
        """The entity names in a question, one completion, bounded by `entity_limit`.

        A missing LLM or a provider error is not fatal: the retriever then runs on
        the raw question, which is what the graph half does when a question names
        nothing the ontology knows.
        """
        if self.llm is None:
            return []
        try:
            predicted = self.llm.structured_predict(
                QueryEntities,
                ENTITY_PROMPT,
                text=query_str,
                limit=self.entity_limit,
            )
        except Exception:
            LOGGER.warning(
                "Entity detection failed for a graph_hybrid query; using the raw question.",
                exc_info=True,
            )
            return []
        names = [str(name).strip() for name in predicted.names if str(name).strip()]
        return names[: self.entity_limit]

    def _chunks_for_triplets(self, triplets: Sequence[NodeWithScore]) -> list[NodeWithScore]:
        """Map reached triplets to their source chunks, best score per chunk."""
        best_score: dict[str, float] = {}
        for triplet in triplets:
            source_id = _chunk_id_of(triplet.node)
            if source_id is None:
                continue
            score = float(triplet.score or 0.0)
            if score > best_score.get(source_id, -1.0):
                best_score[source_id] = score
        if not best_score:
            return []
        nodes = self.graph_store.get_llama_nodes(list(best_score))
        by_id = {node.node_id: node for node in nodes}
        results: list[NodeWithScore] = []
        for source_id, score in best_score.items():
            node = by_id.get(source_id)
            if node is None:
                # A triplet whose source chunk is gone contributes nothing; it is a
                # dropped candidate, not a retrieval error.
                continue
            results.append(NodeWithScore(node=node, score=score))
        return results

    def custom_retrieve(self, query_str: str) -> list[NodeWithScore]:
        """Detect entities, retrieve each one's neighbourhood, return citable chunks."""
        entities = self.detect_entities(query_str)
        queries = entities or [query_str]
        if entities:
            LOGGER.debug("graph_hybrid detected entities: %s", entities)
        hit: dict[str, NodeWithScore] = {}
        for query in queries:
            for node in self.vector_retriever.retrieve(query):
                source_id = _chunk_id_of(node.node)
                if source_id is None:
                    continue
                previous = hit.get(source_id)
                if previous is None or float(node.score or 0.0) > float(previous.score or 0.0):
                    hit[source_id] = node
        return self._chunks_for_triplets(list(hit.values()))

    async def acustom_retrieve(self, query_str: str) -> list[NodeWithScore]:
        return self.custom_retrieve(query_str)


def _chunk_id_of(node: BaseNode) -> str | None:
    """The chunk id a `VectorContextRetriever` result points at, if it points anywhere.

    With `include_text=True` the retriever returns the source chunk itself; with it
    off it returns a triplet node whose `ref_doc_id` is the chunk it came from.
    """
    ref_doc_id = getattr(node, "ref_doc_id", None)
    if ref_doc_id:
        return str(ref_doc_id)
    return None


class GraphHybridRetriever(BaseRetriever):
    """Merge the entity-anchored graph results with the dense results.

    The two sides are not on one scale (a graph similarity against a LanceDB
    distance), and one of them can be degenerate: this embedding model's L2
    distances make the store's ``exp(-distance)`` underflow to exactly ``0.0``.
    So each side is fused by **rank** — see ``_normalised`` — which is meaningful
    on both sides and cannot underflow. A chunk both sides found is kept once,
    with the higher merged score.
    """

    def __init__(
        self,
        graph_retriever: BasePGRetriever,
        dense_retriever: BaseRetriever,
        dense_weight: float = 0.5,
        graph_weight: float = 0.5,
        similarity_top_k: int = 5,
    ) -> None:
        self.graph_retriever = graph_retriever
        self.dense_retriever = dense_retriever
        total = dense_weight + graph_weight
        if total <= 0:
            # Both zero is a config that asked for "no preference", not for nothing.
            self.dense_weight, self.graph_weight = 0.5, 0.5
        else:
            self.dense_weight = dense_weight / total
            self.graph_weight = graph_weight / total
        self.similarity_top_k = similarity_top_k
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        graph_nodes = self.graph_retriever.retrieve(query_bundle)
        dense_nodes = self.dense_retriever.retrieve(query_bundle)
        return merge_results(
            graph_nodes,
            dense_nodes,
            graph_weight=self.graph_weight,
            dense_weight=self.dense_weight,
            top_k=self.similarity_top_k,
        )

    async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        return self._retrieve(query_bundle)


def _normalised(nodes: Sequence[NodeWithScore]) -> list[float]:
    """Rank-scale one side's scores into `[0, 1]`, best first.

    Not min-max on the raw score, because the two sides are not on one scale and
    one of them can be degenerate. A LanceDB cosine index on this embedding model
    reports distances in the hundreds, and the store converts those with
    ``exp(-distance)``, which underflows to exactly ``0.0`` — so every dense hit
    scores zero and a min-max of that list divides by zero. Rank is the part of
    both sides that always means something: a retriever's order.
    """
    if not nodes:
        return []
    total = len(nodes)
    return [(total - position) / total for position in range(total)]


def merge_results(
    graph_nodes: Sequence[NodeWithScore],
    dense_nodes: Sequence[NodeWithScore],
    *,
    graph_weight: float,
    dense_weight: float,
    top_k: int,
) -> list[NodeWithScore]:
    """Weighted union of the two sides, one node per `(doc_id, text)` pair."""
    merged: dict[tuple[str, str], NodeWithScore] = {}
    for nodes, weight in ((graph_nodes, graph_weight), (dense_nodes, dense_weight)):
        for node, scaled in zip(nodes, _normalised(nodes)):
            key = _identity(node.node)
            score = scaled * weight
            previous = merged.get(key)
            if previous is None or score > float(previous.score or 0.0):
                merged[key] = NodeWithScore(node=node.node, score=score)
    ranked = sorted(merged.values(), key=lambda node: float(node.score or 0.0), reverse=True)
    return ranked[:top_k]


def _identity(node: BaseNode) -> tuple[str, str]:
    """What makes two results the same passage: its document and its text."""
    metadata = node.metadata or {}
    doc_id = str(metadata.get("doc_id") or getattr(node, "ref_doc_id", "") or "")
    return doc_id, node.get_content()


def build_graph_hybrid_retriever(
    *,
    graph_store: PropertyGraphStore,
    dense_retriever: BaseRetriever,
    llm: LLM,
    embed_model: BaseEmbedding,
    vector_store: BasePydanticVectorStore | None,
    similarity_top_k: int,
    path_depth: int,
    entity_limit: int,
    dense_weight: float,
    graph_weight: float,
) -> GraphHybridRetriever:
    """Assemble the entity retriever and the merge, as `_build_retriever` uses it."""
    graph_retriever = EntityGraphRetriever(
        graph_store=graph_store,
        include_text=False,
        llm=llm,
        embed_model=embed_model,
        vector_store=vector_store,
        similarity_top_k=similarity_top_k,
        path_depth=path_depth,
        entity_limit=entity_limit,
    )
    return GraphHybridRetriever(
        graph_retriever=graph_retriever,
        dense_retriever=dense_retriever,
        dense_weight=dense_weight,
        graph_weight=graph_weight,
        similarity_top_k=similarity_top_k,
    )
