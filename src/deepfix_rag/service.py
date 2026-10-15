"""Long-lived query service that loads one index and answers retrieval or RAG calls."""

from pathlib import Path

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.indices.base import BaseIndex
from llama_index.core.schema import (
    Document,
    NodeRelationship,
    NodeWithScore,
    RelatedNodeInfo,
)

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.config.settings import LOGGER
from deepfix_rag.pipeline import RAGResult, RAGWorkflow, RetrievalResult


class QueryService:
    """Keep a built index in memory and serve retrieval and answer requests from it.

    ``retrieve`` never calls the generation model, even when ``config.retrieval_only``
    is false. ``answer`` always synthesizes, even when that flag is true.
    """

    def __init__(
        self,
        config: RAGConfig,
        index_dir: Path | str,
        *,
        embed_model: BaseEmbedding | None = None,
        llm: BaseLLM | None = None,
    ) -> None:
        self.config = config.model_copy(deep=True)
        self.config.retrieval.lancedb_index_dir = str(index_dir)
        self.config.retrieval.load_if_exists = True
        self.index_load_count = 0
        self._workflow = RAGWorkflow(config=self.config, embed_model=embed_model, llm=llm)
        self._index = self._load_index()

    def _load_index(self) -> BaseIndex:
        index_dir = self.config.retrieval.lancedb_index_dir
        if not self._workflow._index_exists():
            raise FileNotFoundError(f"No index at {index_dir}.")
        self.index_load_count += 1
        index = self._workflow._load_existing_index()
        self._workflow._index = index
        return index

    async def retrieve(self, query: str) -> list[RetrievalResult]:
        """Return ranked passages. Does not call the generation model."""
        nodes = await self._search(query)
        return self._workflow.convert_nodes_to_results(nodes)

    async def answer(self, query: str) -> RAGResult:
        """Return ranked passages and a generated answer."""
        nodes = await self._search(query)
        generated = await self._generate(query, nodes)
        return RAGResult(
            retrieval_results=self._workflow.convert_nodes_to_results(nodes),
            generated_answer=generated,
        )

    async def _search(self, query: str) -> list[NodeWithScore]:
        retriever = self._workflow._build_retriever(
            index=self._index,
            top_k=self.config.retrieval.top_k,
            language=self.config.retrieval.language,
            use_async=True,
        )
        nodes = await retriever.aretrieve(query)
        reranker = self._workflow._get_reranker()
        if reranker is None:
            return nodes
        return reranker.postprocess_nodes(nodes, query_str=query)

    def replace_document(self, document: Document) -> None:
        """Replace one document's chunks in the current index. Other documents stay put."""
        doc_id = str(document.id_)
        try:
            self._index.delete_ref_doc(doc_id, delete_from_docstore=True)
        except Exception:
            LOGGER.debug("No existing chunks to replace for %s", doc_id, exc_info=True)
        nodes = self._workflow._get_node_parser().get_nodes_from_documents([document])
        for node in nodes:
            metadata = dict(node.metadata or {})
            metadata["doc_id"] = doc_id
            node.metadata = metadata
            node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=doc_id)
        if nodes:
            self._index.insert_nodes(nodes)
        self.refresh_lexical()

    def remove_document(self, doc_id: str) -> None:
        """Remove one document's chunks from the current index."""
        self._index.delete_ref_doc(doc_id, delete_from_docstore=True)
        self.refresh_lexical()

    def refresh_lexical(self) -> None:
        """Make the next lexical search read chunks written since this service loaded."""
        vector_store = getattr(self._index, "vector_store", None)
        table = getattr(vector_store, "table", None)
        if table is not None and hasattr(table, "checkout_latest"):
            table.checkout_latest()
        # A build seeds the docstore with the documents it saw. BM25 prefers that
        # snapshot, so drop it and let the next search rebuild from the table.
        docstore = getattr(self._index, "docstore", None)
        if docstore is None or not hasattr(docstore, "docs"):
            return
        for node_id in list(docstore.docs):
            docstore.delete_document(node_id, raise_error=False)

    def chunk_count(self) -> int:
        """Count chunks currently stored in the serving table."""
        self.refresh_lexical()
        table = self._index.vector_store.table
        if hasattr(table, "count_rows"):
            return int(table.count_rows())
        return len(table.to_arrow())

    def chunk_rows(self) -> list[dict[str, object]]:
        """Return the serving table rows, including ids and embeddings."""
        self.refresh_lexical()
        return list(self._index.vector_store.table.to_arrow().to_pylist())

    async def _generate(self, query: str, nodes: list[NodeWithScore]) -> str | None:
        try:
            synthesizer = self._workflow._get_response_synthesizer()
            response = await synthesizer.asynthesize(query, nodes=nodes)
        except Exception:
            LOGGER.exception("Generation failed for query %r", query)
            return None
        if response is None or response.response is None:
            return None
        text = str(response.response).strip()
        return text or None
