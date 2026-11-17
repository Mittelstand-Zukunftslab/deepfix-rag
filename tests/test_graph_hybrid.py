"""The graph path end to end: ontology extraction, deduplication, and `graph_hybrid`.

These cases need a real FalkorDB instance, because the parts they cover are the
ones a stub cannot tell the truth about: whether strict extraction drops a pair
the ontology forbids, whether a merge re-points the edges a traversal walks, and
whether a retriever returns citable chunks rather than triplet text.

The embedding model is a `MockEmbedding`, so nothing reaches OpenRouter.
`FALKORDB_HOST`/`FALKORDB_PORT` point at the host; `DEEPFIX_FALKORDB_REQUIRED=1`
turns "no host, skipped" into a failure, which is how CI runs this module.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import CompletionResponse, CustomLLM, LLMMetadata
from llama_index.graph_stores.falkordb import FalkorDBPropertyGraphStore
from pydantic import BaseModel
import pytest

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.pipeline import RAGWorkflow
from deepfix_rag.service import QueryService

pytestmark = [pytest.mark.integration]

EMBED_DIM = 16


@pytest.fixture(scope="module")
def falkordb_store() -> FalkorDBPropertyGraphStore:
    """A reachable FalkorDB graph for this module, or a skip (or a failure in CI).

    The graph is a scratch database, not the deployment's: this module deletes
    every node in it, and pointing it at `FALKORDB_DATABASE` would wipe a
    developer's real graph.
    """
    url = f"redis://{settings.FALKORDB_HOST}:{settings.FALKORDB_PORT}"
    database = "deepfix_graph_hybrid_test"
    try:
        store = FalkorDBPropertyGraphStore(url=url, database=database)
        store.structured_query("RETURN 1")
        store.structured_query("MATCH (n) DETACH DELETE n")
    except Exception as exc:
        if os.environ.get("DEEPFIX_FALKORDB_REQUIRED") == "1":
            pytest.fail(
                f"Graph retrieval needs a reachable FalkorDB at {url} because "
                f"DEEPFIX_FALKORDB_REQUIRED=1, and the connection failed: {type(exc).__name__}"
            )
        pytest.skip(
            f"No FalkorDB at {url} ({type(exc).__name__}). Start one with "
            "`docker compose -f compose.graphdb.yml up -d falkordb`, or set "
            "FALKORDB_HOST/FALKORDB_PORT. DEEPFIX_FALKORDB_REQUIRED=1 makes this "
            "a failure instead of a skip."
        )
    previous = settings.FALKORDB_DATABASE
    settings.FALKORDB_DATABASE = database
    try:
        yield store
    finally:
        settings.FALKORDB_DATABASE = previous
        store.structured_query("MATCH (n) DETACH DELETE n")


def test_deduplication_never_merges_identifier_pairs(
    falkordb_store: FalkorDBPropertyGraphStore,
) -> None:
    """Two printed papers whose numbers differ stay two nodes, whatever their text."""
    from deepfix_rag.graph_dedup import plan_merges, read_entities

    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")
    falkordb_store.structured_query(
        "CREATE (a:__Entity__:DRUCKSACHE {id: 'BT-Drucksache 20/123', "
        "name: 'BT-Drucksache 20/123'}), "
        "(b:__Entity__:DRUCKSACHE {id: 'BT-Drucksache 20/124', "
        "name: 'BT-Drucksache 20/124'}), "
        "(c:__Entity__:FRAKTION {id: 'SPD-Fraktion', name: 'SPD-Fraktion'}), "
        "(d:__Entity__:FRAKTION {id: 'SPD Fraktion', name: 'SPD Fraktion'})"
    )
    by_label: dict[str, list[Any]] = {}
    for entity in read_entities(falkordb_store):
        by_label.setdefault(entity.label, []).append(entity)
    embed_model = MockEmbedding(embed_dim=EMBED_DIM)

    papers = plan_merges(
        by_label["DRUCKSACHE"],
        embed_model,
        similarity_threshold=0.0,
        ratio_threshold=0.0,
        max_edit_distance=10,
    )
    assert papers == [], "two different Drucksachen were merged"

    groups = plan_merges(
        by_label["FRAKTION"],
        embed_model,
        similarity_threshold=0.0,
        ratio_threshold=0.0,
        max_edit_distance=10,
    )
    assert len(groups) == 1
    names = {groups[0].canonical.name, *(duplicate.name for duplicate in groups[0].duplicates)}
    assert names == {"SPD-Fraktion", "SPD Fraktion"}
    assert len(groups[0].duplicates) == 1
    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")


def test_a_merge_repoints_edges_and_logs_itself(
    falkordb_store: FalkorDBPropertyGraphStore,
    tmp_path: Path,
) -> None:
    """Merging a duplicate moves its edges to the survivor and appends one log line."""
    from deepfix_rag.graph_dedup import deduplicate_property_graph

    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")
    falkordb_store.structured_query(
        "CREATE (a:__Entity__:FRAKTION {id: 'SPD-Fraktion', name: 'SPD-Fraktion'}), "
        "(b:__Entity__:FRAKTION {id: 'SPD Fraktion', name: 'SPD Fraktion'}), "
        "(c:__Entity__:PERSON {id: 'Alice Berger', name: 'Alice Berger'}), "
        "(c)-[:MEMBER_OF]->(b)"
    )
    log_path = tmp_path / "graph_merges.jsonl"
    merged = deduplicate_property_graph(
        graph_store=falkordb_store,
        embed_model=MockEmbedding(embed_dim=EMBED_DIM),
        log_path=log_path,
        similarity_threshold=0.0,
        ratio_threshold=0.0,
        max_edit_distance=10,
    )

    assert merged == 1
    entities = {
        row["id"]
        for row in falkordb_store.structured_query("MATCH (e:__Entity__) RETURN e.id AS id")
    }
    # Exactly one of the two spellings survives, and the survivor keeps the edges.
    survivors = entities & {"SPD-Fraktion", "SPD Fraktion"}
    assert len(survivors) == 1
    survivor = survivors.pop()
    assert "Alice Berger" in entities
    edges = falkordb_store.structured_query(
        "MATCH (a:__Entity__ {id: 'Alice Berger'})-[r]->(b) RETURN type(r) AS type, b.id AS target"
    )
    assert {"type": "MEMBER_OF", "target": survivor} in edges

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert set(records[0]) == {
        "label",
        "canonical",
        "merged",
        "similarity",
        "name_ratio",
        "edit_distance",
        "reason",
    }
    assert records[0]["label"] == "FRAKTION"
    assert records[0]["canonical"] == survivor
    assert records[0]["merged"] in {"SPD-Fraktion", "SPD Fraktion"}
    assert records[0]["merged"] != survivor
    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")


class _StubKnowledgeLLM(CustomLLM):
    """Fixed triplets for extraction and fixed entity names for query detection.

    One stub covers both graph LLM calls, which is what makes the build and the
    query assertion deterministic: the corpus below is written so the triplets are
    exactly the facts the test then looks for.
    """

    triplets: list[dict[str, Any]] = []
    entity_names: list[str] = []
    entity_calls: int = 0

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(model_name="stub-knowledge", is_chat_model=True)

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(text="")

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> Any:
        yield CompletionResponse(text="")

    def structured_predict(
        self,
        output_cls: type[BaseModel],
        prompt: Any,
        llm_kwargs: dict[str, Any] | None = None,
        **prompt_args: Any,
    ) -> BaseModel:
        if output_cls.__name__ == "QueryEntities":
            self.entity_calls += 1
            return output_cls.model_validate({"names": list(self.entity_names)})
        return output_cls.model_validate({"triplets": list(self.triplets)})

    async def astructured_predict(
        self,
        output_cls: type[BaseModel],
        prompt: Any,
        llm_kwargs: dict[str, Any] | None = None,
        **prompt_args: Any,
    ) -> BaseModel:
        return self.structured_predict(output_cls, prompt, llm_kwargs, **prompt_args)


def _triplets() -> list[dict[str, Any]]:
    def entity(kind: str, name: str) -> dict[str, str]:
        return {"type": kind, "name": name}

    return [
        {
            "subject": entity("PERSON", "Alice Berger"),
            "relation": {"type": "MEMBER_OF"},
            "object": entity("FRAKTION", "SPD-Fraktion"),
        },
        {
            "subject": entity("DRUCKSACHE", "BT-Drucksache 20/123"),
            "relation": {"type": "SUBMITTED_BY"},
            "object": entity("FRAKTION", "SPD-Fraktion"),
        },
        {
            "subject": entity("DRUCKSACHE", "BT-Drucksache 20/124"),
            "relation": {"type": "SUBMITTED_BY"},
            "object": entity("FRAKTION", "SPD Fraktion"),
        },
        {
            "subject": entity("VORGANG", "Vorgang 42"),
            "relation": {"type": "ASSIGNED_TO"},
            "object": entity("AUSSCHUSS", "Auswärtiger Ausschuss"),
        },
        # Outside the pair allow-list: a committee cannot submit a policy topic.
        {
            "subject": entity("AUSSCHUSS", "Auswärtiger Ausschuss"),
            "relation": {"type": "SUBMITTED_BY"},
            "object": entity("THEMA", "UN-Resolution"),
        },
    ]


CORPUS = [
    {
        "doc_id": "doc_graph_001",
        "text": (
            "Alice Berger ist Mitglied der SPD-Fraktion. Die Fraktion hat die "
            "Drucksache 20/123 eingebracht."
        ),
    },
    {
        "doc_id": "doc_graph_002",
        "text": (
            "Der Vorgang 42 wurde an den Auswärtigen Ausschuss überwiesen. Die "
            "Drucksache 20/124 behandelt die UN-Resolution."
        ),
    },
    {
        "doc_id": "doc_graph_003",
        "text": (
            "Die SPD-Fraktion hat BT-Drucksache 20/124 eingebracht, die sich auf "
            "die UN-Resolution bezieht."
        ),
    },
]


def _write_corpus(tmp_path: Path) -> Path:
    path = tmp_path / "graph_corpus.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in CORPUS) + "\n")
    return path


def _config(corpus: Path, tmp_path: Path) -> RAGConfig:
    return RAGConfig.from_yaml(
        "retrieval:\n"
        "  backend: graph_hybrid\n"
        "  graph_store: falkordb\n"
        "  graph_extraction_schema: bundestag\n"
        "  graph_traversal_hops: 2\n"
        "  graph_query_entity_limit: 5\n"
        "  top_k: 5\n"
        "  lancedb_index_dir: " + str(tmp_path / "index") + "\n"
        "  load_if_exists: false\n"
        "rerank:\n"
        "  enabled: false\n"
        "data:\n"
        f"  path_or_repo: {corpus}\n"
        "retrieval_only: true\n"
    )


def _build(config: RAGConfig, llm: CustomLLM) -> RAGWorkflow:
    workflow = RAGWorkflow(config=config, llm=llm, embed_model=MockEmbedding(embed_dim=EMBED_DIM))

    async def _run() -> None:
        await workflow.run(ingest=True)

    asyncio.run(_run())
    return workflow


def test_graph_hybrid_extracts_dedupes_and_returns_citable_chunks(
    falkordb_store: FalkorDBPropertyGraphStore,
    tmp_path: Path,
) -> None:
    """A small Bundestag graph is built, deduped, and queried as citable chunks."""
    corpus = _write_corpus(tmp_path)
    config = _config(corpus, tmp_path)
    llm = _StubKnowledgeLLM(triplets=_triplets(), entity_names=["Alice Berger"])
    workflow = _build(config, llm)

    # Strict extraction kept the allowed pairs and dropped the committee-to-topic one.
    labels = {
        tuple(sorted(row["labels"]))
        for row in falkordb_store.structured_query(
            "MATCH (e:__Entity__) RETURN [l IN labels(e) WHERE l <> '__Entity__'] AS labels"
        )
    }
    assert ("PERSON",) in labels
    assert ("DRUCKSACHE",) in labels

    # Deduplication merged the same group written two ways, and left 20/123 and 20/124 apart.
    entities = {
        row["id"]
        for row in falkordb_store.structured_query("MATCH (e:__Entity__) RETURN e.id AS id")
    }
    assert "BT-Drucksache 20/123" in entities
    assert "BT-Drucksache 20/124" in entities

    log_path = Path(config.retrieval.lancedb_index_dir) / "graph_merges.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1, records
    record = records[0]
    assert {record["canonical"], record["merged"]} == {"SPD-Fraktion", "SPD Fraktion"}
    assert record["merged"] not in entities

    # The dense half exists, so graph_hybrid is not silently graph-only.
    assert workflow._count_index_chunks(workflow._index) > 0

    retriever = workflow._build_retriever(workflow._index, top_k=config.retrieval.top_k)
    nodes = asyncio.run(retriever.aretrieve("Was hat Alice Berger eingebracht?"))
    passages = workflow.convert_nodes_to_results(nodes)
    assert passages, "graph_hybrid returned no passages"
    assert len(passages) <= config.retrieval.top_k
    for passage in passages:
        assert " -> " not in passage.text, "a triplet line is not a citable chunk"
        assert passage.doc_id.startswith("doc_graph_")
    # The chunk reached from both sides is one passage, not two.
    keys = [(passage.doc_id, passage.text) for passage in passages]
    assert len(keys) == len(set(keys))
    assert llm.entity_calls >= 1

    # A champion in this state is what Ask serves: QueryService must load it
    # without rebuilding, and answer from both halves.
    service = QueryService(
        config,
        config.retrieval.lancedb_index_dir,
        embed_model=MockEmbedding(embed_dim=EMBED_DIM),
        llm=_StubKnowledgeLLM(triplets=_triplets(), entity_names=["Alice Berger"]),
    )
    assert service.index_load_count == 1
    assert service.chunk_count() > 0
    served = asyncio.run(service.retrieve("Was hat Alice Berger eingebracht?"))
    assert served and all(passage.doc_id.startswith("doc_graph_") for passage in served)
    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")


def test_graph_hybrid_config_requires_a_graph_store(default_config_path: Path) -> None:
    """`graph_hybrid` with no store fails in the config, not at query time."""
    config = RAGConfig.from_yaml(default_config_path)
    assert config.retrieval.graph_store.value == "none"
    with pytest.raises(ValueError, match="graph_hybrid"):
        RAGConfig.model_validate(
            {
                **config.model_dump(),
                "retrieval": {**config.retrieval.model_dump(), "backend": "graph_hybrid"},
            }
        )
