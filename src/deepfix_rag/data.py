from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, TypeVar

from chonkie import RecursiveChunker
from datasets import load_dataset
from llama_index.core import SimpleDirectoryReader
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.node_parser import (
    HierarchicalNodeParser,
    NodeParser,
    SemanticSplitterNodeParser,
    SentenceSplitter,
    TokenTextSplitter,
    get_leaf_nodes,
)
from llama_index.core.node_parser.interface import MetadataAwareTextSplitter
from llama_index.core.schema import Document
from llama_index.embeddings.openai import OpenAIEmbedding
import pandas as pd
from pydantic import Field

from .config.schema import (
    ChunkingConfig,
    ChunkingStrategy,
    DataLoaderConfig,
    DataPreset,
    DataSourceType,
)
from .config.settings import LOGGER, settings

_CHUNK_ID_SUFFIX = re.compile(r"::chunk_\d+$")
T = TypeVar("T")


def _normalize_doc_id(value: str) -> str:
    """Return parent doc_id, stripping a trailing `::chunk_{n}` suffix if present."""
    if not value:
        return value
    return _CHUNK_ID_SUFFIX.sub("", str(value))


def _select_by_ids_and_limit(
    items: Iterable[T],
    *,
    get_id: Callable[[T], str],
    doc_ids: list[str] | None,
    limit: int | None,
    include_required: bool = False,
) -> list[T]:
    """Apply optional doc_id filtering and a record budget while streaming.

    Filter mode (QA): keep rows whose id is in ``doc_ids`` (if given) and stop at
    ``limit``. Include mode (corpus): keep every required id, then fill remaining
    slots up to ``max(limit, len(required))``. Required ids are never dropped
    because ``limit`` is too small.
    """
    required = {_normalize_doc_id(doc_id) for doc_id in doc_ids} if doc_ids else set()

    if include_required and required:
        required_items: dict[str, T] = {}
        fillers: list[T] = []
        filler_budget = (
            None if limit is None else max(0, max(limit, len(required)) - len(required))
        )
        for item in items:
            item_id = _normalize_doc_id(get_id(item))
            if item_id in required:
                if item_id not in required_items:
                    required_items[item_id] = item
            elif filler_budget is None or len(fillers) < filler_budget:
                fillers.append(item)

            if len(required_items) == len(required) and (
                filler_budget is not None and len(fillers) >= filler_budget
            ):
                break

        missing = required - set(required_items)
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise ValueError(
                f"Corpus is missing {len(missing)}/{len(required)} required "
                f"doc_ids: {missing_list}"
            )

        ordered: list[T] = []
        seen: set[str] = set()
        for doc_id in doc_ids or []:
            normalized = _normalize_doc_id(doc_id)
            if normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(required_items[normalized])
        ordered.extend(fillers)
        return ordered

    selected: list[T] = []
    for item in items:
        item_id = _normalize_doc_id(get_id(item))
        if required and item_id not in required:
            continue
        selected.append(item)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def _default_embed_model() -> BaseEmbedding:
    """Build the project default OpenAI-compatible embedding model from settings."""
    return OpenAIEmbedding(
        model_name=settings.EMBEDDING_MODEL,
        api_base=settings.EMBEDDING_BASE_URL,
        api_key=settings.EMBEDDING_API_KEY,
    )


class RecursiveChonkieParser(MetadataAwareTextSplitter):
    """LlamaIndex adapter for Chonkie recursive chunking.

    Equivalent to ``Chunker("recursive", chunk_size=...)`` from
    ``llama_index.node_parser.chonkie``, using ``chonkie.RecursiveChunker``.
    """

    chunk_size: int = Field(default=2048, ge=1)
    _recursive_chunker: RecursiveChunker | None = None

    def __init__(self, chunk_size: int = 2048, **kwargs: Any) -> None:
        super().__init__(chunk_size=chunk_size, **kwargs)
        self._recursive_chunker = RecursiveChunker(chunk_size=chunk_size)

    def split_text(self, text: str) -> list[str]:
        """Split text with Chonkie recursive rules."""
        if text == "":
            return [text]
        if self._recursive_chunker is None:
            self._recursive_chunker = RecursiveChunker(chunk_size=self.chunk_size)
        chunks = self._recursive_chunker.chunk(text)
        if isinstance(chunks, list):
            return [chunk.text if hasattr(chunk, "text") else str(chunk) for chunk in chunks]
        return [chunks.text if hasattr(chunks, "text") else str(chunks)]

    def split_text_metadata_aware(self, text: str, metadata_str: str) -> list[str]:
        """Recursive Chonkie splitting does not reserve tokens for metadata."""
        return self.split_text(text)


def build_node_parser(
    chunking: ChunkingConfig,
    embed_model: BaseEmbedding | None = None,
) -> NodeParser:
    """Construct a LlamaIndex node parser from chunking configuration."""
    if chunking.strategy == ChunkingStrategy.RECURSIVE:
        return RecursiveChonkieParser(chunk_size=chunking.size)
    if chunking.strategy == ChunkingStrategy.SENTENCE:
        return SentenceSplitter(chunk_size=chunking.size, chunk_overlap=chunking.overlap)
    if chunking.strategy == ChunkingStrategy.FIXED:
        return TokenTextSplitter(chunk_size=chunking.size, chunk_overlap=chunking.overlap)
    if chunking.strategy == ChunkingStrategy.SEMANTIC:
        return SemanticSplitterNodeParser(
            buffer_size=chunking.semantic_buffer_size,
            breakpoint_percentile_threshold=chunking.semantic_breakpoint_percentile,
            embed_model=embed_model or _default_embed_model(),
        )
    if chunking.strategy == ChunkingStrategy.HIERARCHICAL:
        return HierarchicalNodeParser.from_defaults(
            chunk_sizes=chunking.resolved_hierarchical_chunk_sizes()
        )
    raise ValueError(f"Unknown chunking strategy: {chunking.strategy}")


class QAValidationError(ValueError):
    """A QA row cannot be scored because its labels are inconsistent."""


def _unique_doc_ids(values: list[Any]) -> list[str]:
    """Drop blanks and duplicates while keeping the first occurrence of each id."""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        doc_id = str(value).strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        unique.append(doc_id)
    return unique


def qa_record_from_row(row: dict[str, Any]) -> QARecord:
    """Build one eval record from a JSON object.

    A row carries either ``doc_id`` or ``doc_ids``, never both. ``negative_doc_ids``
    is optional. A missing ``answer`` stays missing so it is not invented as a label.
    """
    if "doc_id" in row and "doc_ids" in row:
        raise QAValidationError("A row cannot include both doc_id and doc_ids.")
    if "query_id" not in row or "query" not in row:
        raise QAValidationError("A row must include query_id and query.")

    if "doc_ids" in row:
        raw_ids = row["doc_ids"]
        if not isinstance(raw_ids, list):
            raise QAValidationError("doc_ids must be a list of document ids.")
        doc_ids = _unique_doc_ids(raw_ids)
        doc_id = doc_ids[0] if doc_ids else ""
    elif "doc_id" in row and row["doc_id"] is not None:
        doc_id = str(row["doc_id"]).strip()
        doc_ids = [doc_id] if doc_id else []
    else:
        doc_id = ""
        doc_ids = []

    negative_doc_ids: list[str] | None
    if "negative_doc_ids" in row and row["negative_doc_ids"] is not None:
        raw_negatives = row["negative_doc_ids"]
        if not isinstance(raw_negatives, list):
            raise QAValidationError("negative_doc_ids must be a list of document ids.")
        negative_doc_ids = _unique_doc_ids(raw_negatives)
    else:
        negative_doc_ids = None

    overlap = set(doc_ids) & set(negative_doc_ids or [])
    if overlap:
        listed = ", ".join(sorted(overlap))
        raise QAValidationError(f"The same document id cannot be relevant and negative: {listed}.")

    raw_answer = row.get("answer", None)
    answer = None if raw_answer is None or str(raw_answer).strip() == "" else str(raw_answer)
    unanswerable = bool(row.get("unanswerable", False))
    return QARecord(
        query_id=str(row["query_id"]),
        query=str(row["query"]),
        answer=answer,
        doc_id=doc_id,
        doc_ids=doc_ids,
        negative_doc_ids=negative_doc_ids,
        unanswerable=unanswerable,
    )


@dataclass
class QARecord:
    """Normalized QA evaluation record loaded across benchmark datasets."""

    query_id: str
    query: str
    answer: str | None = None
    doc_id: str = ""
    doc_ids: list[str] = field(default_factory=list)
    negative_doc_ids: list[str] | None = None
    unanswerable: bool = False

    def __post_init__(self) -> None:
        if self.doc_ids:
            self.doc_ids = _unique_doc_ids(self.doc_ids)
        elif self.doc_id.strip():
            self.doc_ids = [self.doc_id.strip()]
        else:
            self.doc_ids = []
        self.doc_id = self.doc_ids[0] if self.doc_ids else ""
        if self.negative_doc_ids is not None:
            self.negative_doc_ids = _unique_doc_ids(self.negative_doc_ids)
            overlap = set(self.doc_ids) & set(self.negative_doc_ids)
            if overlap:
                listed = ", ".join(sorted(overlap))
                raise QAValidationError(
                    f"The same document id cannot be relevant and negative: {listed}."
                )
        if self.answer is not None and not self.answer.strip():
            self.answer = None
        # Empty ground truth, with no judged negatives, is the existing unanswerable mark.
        if self.negative_doc_ids is None and not self.doc_ids:
            self.unanswerable = True

    def qrel_rows(self) -> list[dict[str, Any]]:
        """One qrel per judged document. Relevant is 1 and explicit negatives are 0."""
        rows = [
            {
                "qid": self.query_id,
                "docno": doc_id,
                "label": 1,
                "rank": 1,
                "query": self.query,
            }
            for doc_id in self.doc_ids
        ]
        if self.negative_doc_ids is not None:
            rows.extend(
                {
                    "qid": self.query_id,
                    "docno": doc_id,
                    "label": 0,
                    "rank": 1,
                    "query": self.query,
                }
                for doc_id in self.negative_doc_ids
            )
        return rows

    def to_pyterrier(self) -> dict[str, Any]:
        rows = self.qrel_rows()
        if rows:
            return rows[0]
        return {
            "qid": self.query_id,
            "docno": self.doc_id,
            "label": 1,
            "rank": 1,
            "query": self.query,
        }

    def to_export_row(self) -> dict[str, Any]:
        """Serialize in the shape this loader accepts again."""
        if (
            len(self.doc_ids) == 1
            and self.negative_doc_ids is None
            and self.answer is not None
            and not self.unanswerable
        ):
            return {
                "query_id": self.query_id,
                "query": self.query,
                "answer": self.answer,
                "doc_id": self.doc_ids[0],
            }
        row: dict[str, Any] = {
            "query_id": self.query_id,
            "query": self.query,
            "doc_ids": list(self.doc_ids),
        }
        if self.answer is not None:
            row["answer"] = self.answer
        if self.negative_doc_ids is not None:
            row["negative_doc_ids"] = list(self.negative_doc_ids)
        if self.unanswerable:
            row["unanswerable"] = True
        return row


class BaseLoaderStrategy(ABC):
    """Abstract interface for all data loaders."""

    @abstractmethod
    def load_corpus(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[Document]:
        """Load and return corpus documents."""
        raise NotImplementedError("Subclasses must implement load_corpus()")

    @abstractmethod
    def load_qa(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[QARecord]:
        """Load and return QA pairs."""
        raise NotImplementedError("Subclasses must implement load_qa()")


class BundestagLoader(BaseLoaderStrategy):
    """Loader dedicated to the German Bundestag parliamentary benchmark datasets."""

    CORPUS_REPO = "wirthual/dip-bundestag"
    QA_REPO = "wirthual/dip-bundestag-qa"

    def load_corpus(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[Document]:
        repo = self.CORPUS_REPO
        split = "train"
        LOGGER.info(f"Downloading HuggingFace Bundestag corpus '{repo}' (split: {split})...")
        hf_data = load_dataset(repo, split=split)

        def _docs() -> Iterable[Document]:
            for row in hf_data:
                doc_id = str(row["doc_id"])
                yield Document(
                    text=row["text"],
                    id_=doc_id,
                    metadata={"source": repo, "split": split, "doc_id": doc_id},
                )

        return _select_by_ids_and_limit(
            _docs(),
            get_id=lambda doc: str(doc.id_),
            doc_ids=doc_ids,
            limit=config.limit,
            include_required=doc_ids is not None,
        )

    @staticmethod
    def make_qa_record(row: dict) -> QARecord:
        """Normalize and construct a benchmark QA record from arbitrary source mappings."""
        return QARecord(
            query_id=str(row["index"]),
            query=str(row["question"]),
            answer=str(row["answer"]),
            doc_id=str(row["doc_id"]),
        )

    def load_qa(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[QARecord]:
        repo = self.QA_REPO
        split = config.split or "train"
        LOGGER.info(f"Downloading HuggingFace Bundestag QA '{repo}' (split: {split})...")
        ds = load_dataset(repo, split=split)

        def _rows() -> Iterable[QARecord]:
            for row in ds:
                yield self.make_qa_record(row)

        return _select_by_ids_and_limit(
            _rows(),
            get_id=lambda record: record.doc_id,
            doc_ids=doc_ids,
            limit=config.limit,
        )


class LegalRagBenchLoader(BaseLoaderStrategy):
    """Loader dedicated to the Legal RAG Bench benchmark dataset."""

    REPO = "isaacus/legal-rag-bench"

    def load_corpus(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[Document]:
        split = "test"
        subset = "corpus"
        LOGGER.info(
            f"Downloading HuggingFace Legal RAG Bench corpus '{self.REPO}' (subset: {subset}, split: {split})..."
        )
        try:
            hf_data = load_dataset(self.REPO, subset, split=split)
        except Exception:
            hf_data = load_dataset(self.REPO, split=split)

        def _docs() -> Iterable[Document]:
            for row in hf_data:
                doc_id = str(row["id"])
                metadata = {
                    "source": self.REPO,
                    "split": split,
                    "doc_id": doc_id,
                    "title": row["title"],
                    "footnotes": row["footnotes"],
                }
                yield Document(text=row["text"], id_=doc_id, metadata=metadata)

        return _select_by_ids_and_limit(
            _docs(),
            get_id=lambda doc: str(doc.id_),
            doc_ids=doc_ids,
            limit=config.limit,
            include_required=doc_ids is not None,
        )

    @staticmethod
    def make_qa_record(row: dict) -> QARecord:
        """Normalize and construct a benchmark QA record from arbitrary source mappings."""
        return QARecord(
            query_id=str(row["id"]),
            query=str(row["question"]),
            answer=str(row["answer"]),
            doc_id=str(row["relevant_passage_id"]),
        )

    def load_qa(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[QARecord]:
        split = "test"
        subset = "qa"
        LOGGER.info(
            f"Downloading HuggingFace Legal RAG Bench QA '{self.REPO}' (subset: {subset}, split: {split})..."
        )
        try:
            ds = load_dataset(self.REPO, subset, split=split)
        except Exception:
            ds = load_dataset(self.REPO, split=split)

        def _rows() -> Iterable[QARecord]:
            for row in ds:
                yield self.make_qa_record(row)

        return _select_by_ids_and_limit(
            _rows(),
            get_id=lambda record: record.doc_id,
            doc_ids=doc_ids,
            limit=config.limit,
        )


class HuggingFaceLoader(BaseLoaderStrategy):
    """Router strategy delegating to BundestagLoader or LegalRagBenchLoader."""

    def __init__(self):
        self._bundestag_loader = BundestagLoader()
        self._legal_rag_bench_loader = LegalRagBenchLoader()

    def _resolve_loader(self, config: DataLoaderConfig) -> BaseLoaderStrategy:
        _, repo = DataPreset.resolve(config.path_or_repo)
        if repo == LegalRagBenchLoader.REPO or "legal-rag-bench" in repo.lower():
            return self._legal_rag_bench_loader
        return self._bundestag_loader

    def load_corpus(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[Document]:
        return self._resolve_loader(config).load_corpus(config, doc_ids=doc_ids)

    def load_qa(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[QARecord]:
        return self._resolve_loader(config).load_qa(config, doc_ids=doc_ids)


class LocalFileLoader(BaseLoaderStrategy):
    """Loader for local JSON and JSONL benchmark files."""

    def load_corpus(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[Document]:
        LOGGER.info(f"Loading local file '{config.path_or_repo}'...")

        def _docs() -> Iterable[Document]:
            with open(config.path_or_repo, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    item = json.loads(line_str)
                    doc_id = str(item.get("id", item.get("doc_id", "")))
                    text = item.get("text", item.get("content", ""))
                    yield Document(
                        text=text,
                        id_=doc_id,
                        metadata={"source": config.path_or_repo, "doc_id": doc_id},
                    )

        return _select_by_ids_and_limit(
            _docs(),
            get_id=lambda doc: str(doc.id_),
            doc_ids=doc_ids,
            limit=config.limit,
            include_required=doc_ids is not None,
        )

    @staticmethod
    def make_qa_record(row: dict) -> QARecord:
        """Normalize a local JSONL QA row, including multi-label feedback rows."""
        return qa_record_from_row(row)

    def load_qa(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[QARecord]:
        LOGGER.info(f"Loading local QA file '{config.path_or_repo}'...")
        with open(config.path_or_repo, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()

        if not content:
            return []

        rows: list[QARecord] = []
        if content.startswith("["):
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    rows = [self.make_qa_record(r) for r in data]
            except json.JSONDecodeError:
                rows = []

        if not rows:
            for line in content.splitlines():
                line_str = line.strip()
                if line_str:
                    r = json.loads(line_str)
                    rows.append(self.make_qa_record(r))

        return _select_by_ids_and_limit(
            rows,
            get_id=lambda record: record.doc_id,
            doc_ids=doc_ids,
            limit=config.limit,
        )


class DirectoryLoader(BaseLoaderStrategy):
    """Loader scanning local directories for documents or QA files."""

    def load_corpus(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[Document]:
        LOGGER.info(f"Scanning local directory '{config.path_or_repo}'...")
        docs = SimpleDirectoryReader(config.path_or_repo).load_data()
        return _select_by_ids_and_limit(
            docs,
            get_id=lambda doc: str((doc.metadata or {}).get("doc_id") or doc.id_),
            doc_ids=doc_ids,
            limit=config.limit,
            include_required=doc_ids is not None,
        )

    def load_qa(
        self, config: DataLoaderConfig, doc_ids: list[str] | None = None
    ) -> list[QARecord]:
        dir_path = Path(config.path_or_repo)
        LOGGER.info(f"Scanning local directory '{dir_path}' for QA dataset...")

        for candidate_name in ("qa.jsonl", "queries.jsonl", "test.jsonl", "dev.jsonl", "qa.json"):
            candidate_file = dir_path / candidate_name
            if candidate_file.is_file():
                file_config = config.model_copy(
                    update={
                        "source_type": DataSourceType.LOCAL_FILE,
                        "path_or_repo": str(candidate_file),
                    }
                )
                return LocalFileLoader().load_qa(file_config, doc_ids=doc_ids)

        raise FileNotFoundError(
            f"No QA dataset file (qa.jsonl, queries.jsonl, test.jsonl, etc.) found in directory '{dir_path}'."
        )


class DataLoader:
    """
    Unified Data Loader Facade.
    Applies Separation of Concerns by routing to specific strategies internally.
    """

    def __init__(self, config: DataLoaderConfig) -> None:
        self.config = config
        self.strategies = {
            DataSourceType.HUGGINGFACE: HuggingFaceLoader(),
            DataSourceType.LOCAL_FILE: LocalFileLoader(),
            DataSourceType.DIRECTORY: DirectoryLoader(),
        }

    def _strategy(self) -> BaseLoaderStrategy:
        strategy = self.strategies.get(self.config.source_type)
        if not strategy:
            raise ValueError(f"Unknown source type: {self.config.source_type}")
        return strategy

    def load_corpus(self, doc_ids: list[str] | None = None) -> list[Document]:
        """Load corpus documents, optionally requiring ``doc_ids`` plus fillers."""
        docs = self._strategy().load_corpus(self.config, doc_ids=doc_ids)
        LOGGER.info(f"Successfully loaded {len(docs)} corpus documents.")
        return docs

    @staticmethod
    def parent_doc_id(value: str) -> str:
        """Return parent doc_id, stripping a trailing `::chunk_{n}` suffix if present."""
        return _normalize_doc_id(value)

    @staticmethod
    def chunk_docs(
        documents: list[Document],
        chunking: ChunkingConfig | None = None,
    ) -> list[Document]:
        """Split corpus documents while keeping parent `doc_id` as `Document.id_`.

        Unique per-span identity is stored only in `metadata["chunk_id"]`.
        """
        if not documents:
            return []

        cfg = chunking or ChunkingConfig()
        splitter = build_node_parser(cfg)
        chunked: list[Document] = []
        for doc in documents:
            parent_id = DataLoader.parent_doc_id(
                str((doc.metadata or {}).get("doc_id") or doc.id_)
            )
            nodes = splitter.get_nodes_from_documents([doc])
            if cfg.strategy == ChunkingStrategy.HIERARCHICAL:
                nodes = get_leaf_nodes(nodes)
            for idx, node in enumerate(nodes):
                metadata = dict(doc.metadata or {})
                metadata.update(node.metadata or {})
                metadata["doc_id"] = parent_id
                metadata["chunk_id"] = f"{parent_id}::chunk_{idx}"
                chunked.append(
                    Document(
                        text=node.get_content(),
                        id_=parent_id,
                        metadata=metadata,
                    )
                )
        return chunked

    def load_qa(self, doc_ids: list[str] | None = None) -> list[QARecord]:
        """Load QA pairs, optionally filtering to gold ``doc_ids`` within ``limit``."""
        qa_pairs = self._strategy().load_qa(self.config, doc_ids=doc_ids)
        LOGGER.info(f"Successfully loaded {len(qa_pairs)} QA pairs.")
        return qa_pairs

    @staticmethod
    def to_qrels_df(qa_pairs: list[QARecord]) -> pd.DataFrame:
        """Convert a list of QARecord objects to a PyTerrier-compatible qrels DataFrame.

        Returns DataFrame with columns: ['qid', 'docno', 'label', 'rank', 'query'] where label=1.
        """
        rows: list[dict[str, Any]] = [qrel for record in qa_pairs for qrel in record.qrel_rows()]

        if not rows:
            return pd.DataFrame(columns=["qid", "docno", "label", "rank"]).astype(
                {"qid": "str", "docno": "str", "label": "int8", "rank": "int8"}
            )
        df = pd.DataFrame(rows)
        df["qid"] = df["qid"].astype(str)
        df["docno"] = df["docno"].astype(str)
        df["label"] = df["label"].astype("int8")
        df["rank"] = df["rank"].astype("int8")
        return df

    def load_qrels(self, doc_ids: list[str] | None = None) -> pd.DataFrame:
        """Load QA pairs and return as a PyTerrier qrels DataFrame."""
        qa_pairs = self.load_qa(doc_ids=doc_ids)
        return self.to_qrels_df(qa_pairs)

    def collate_documents(self, docs: list[Document], join_str: str = "\n\n") -> list[Document]:
        raise NotImplementedError("Not implemented yet.")

    @staticmethod
    def load_and_save(
        name_or_repo: str,
        split: str = "train",
        data_root: str | Path | None = None,
        limit: int | None = None,
        subset: str | None = None,
        corpus_only: bool = False,
    ) -> tuple[Path, Path | None]:
        """
        Loads documents and QA records for a dataset and exports them into standard files:
          <data_root>/<dataset_name>/<split>/corpus.jsonl
          <data_root>/<dataset_name>/<split>/qa.jsonl
        """
        root = Path(data_root or settings.DATA_PROCESSES_DIR)
        preset = DataPreset.find_preset(name_or_repo)
        _source_type, repo = DataPreset.resolve(name_or_repo)
        dataset_name = str(name_or_repo).removeprefix("HF:").split("/")[-1].lower()

        target_dir = root / dataset_name / split
        target_dir.mkdir(parents=True, exist_ok=True)

        is_corpus_only = (
            corpus_only
            or (preset is not None and preset.is_corpus_only())
            or repo == "wirthual/dip-bundestag"
            or dataset_name == "bundestag-corpus"
            or subset == "corpus"
        )

        corpus_repo = (
            BundestagLoader.CORPUS_REPO
            if (
                repo in (BundestagLoader.CORPUS_REPO, BundestagLoader.QA_REPO)
                or "bundestag" in dataset_name
            )
            else repo
        )

        corpus_config = DataLoaderConfig(
            path_or_repo=corpus_repo,
            split=split,
            limit=limit,
        )
        docs = DataLoader(corpus_config).load_corpus()

        suffix = f"_{limit}" if limit else ""
        corpus_file = target_dir / f"corpus{suffix}.jsonl"
        with open(corpus_file, "w", encoding="utf-8") as f:
            f.writelines(
                json.dumps({"doc_id": doc.id_, "text": doc.text}, ensure_ascii=False) + "\n"
                for doc in docs
            )

        qa_file: Path | None = None
        if not is_corpus_only:
            qa_repo = (
                BundestagLoader.QA_REPO
                if (
                    repo in (BundestagLoader.CORPUS_REPO, BundestagLoader.QA_REPO)
                    or "bundestag" in dataset_name
                )
                else repo
            )
            qa_config = DataLoaderConfig(
                path_or_repo=qa_repo,
                split=split,
                limit=limit,
            )
            qa_pairs = DataLoader(qa_config).load_qa()
            if qa_pairs:
                qa_file = target_dir / f"qa{suffix}.jsonl"
                with open(qa_file, "w", encoding="utf-8") as f:
                    f.writelines(
                        json.dumps(qa.to_export_row(), ensure_ascii=False) + "\n"
                        for qa in qa_pairs
                    )

        return corpus_file, qa_file


__all__ = [
    "BaseLoaderStrategy",
    "BundestagLoader",
    "LegalRagBenchLoader",
    "HuggingFaceLoader",
    "LocalFileLoader",
    "DirectoryLoader",
    "DataLoader",
    "DataLoaderConfig",
    "DataSourceType",
    "DataPreset",
    "QARecord",
    "build_node_parser",
    "RecursiveChonkieParser",
]
