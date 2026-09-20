from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from datasets import load_dataset
from llama_index.core import SimpleDirectoryReader
from llama_index.core.schema import Document
import pandas as pd

from .config.schema import DataLoaderConfig, DataPreset, DataSourceType
from .config.settings import LOGGER, settings


@dataclass
class QARecord:
    """Normalized QA evaluation record loaded across benchmark datasets."""

    query_id: str
    query: str
    answer: str
    doc_id: str

    def to_pyterrier(self) -> dict[str, Any]:
        return {
            "qid": self.query_id,
            "docno": self.doc_id,
            "label": 1,
            "rank": 1,
            "query": self.query,
        }


class BaseLoaderStrategy(ABC):
    """Abstract interface for all data loaders."""

    @abstractmethod
    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        """Load and return corpus documents."""
        raise NotImplementedError("Subclasses must implement load_corpus()")

    @abstractmethod
    def load_qa(self, config: DataLoaderConfig) -> list[QARecord]:
        """Load and return QA pairs."""
        raise NotImplementedError("Subclasses must implement load_qa()")


class BundestagLoader(BaseLoaderStrategy):
    """Loader dedicated to the German Bundestag parliamentary benchmark datasets."""

    CORPUS_REPO = "wirthual/dip-bundestag"
    QA_REPO = "wirthual/dip-bundestag-qa"

    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        repo = self.CORPUS_REPO
        split = "train"
        LOGGER.info(f"Downloading HuggingFace Bundestag corpus '{repo}' (split: {split})...")
        hf_data = load_dataset(repo, split=split)
        if config.limit is not None and hasattr(hf_data, "select"):
            hf_data = hf_data.select(range(min(len(hf_data), config.limit)))

        docs_list = []
        for i, row in enumerate(hf_data):
            text = row["text"]
            doc_id = str(row["doc_id"])
            metadata = {"source": repo, "split": split}
            docs_list.append(Document(text=text, id_=doc_id, metadata=metadata))

        return docs_list

    @staticmethod
    def make_qa_record(row: dict) -> QARecord:
        """Normalize and construct a benchmark QA record from arbitrary source mappings."""
        return QARecord(
            query_id=str(row["index"]),
            query=str(row["question"]),
            answer=str(row["answer"]),
            doc_id=str(row["doc_id"]),
        )

    def load_qa(self, config: DataLoaderConfig) -> list[QARecord]:
        repo = self.QA_REPO
        split = config.split or "train"
        LOGGER.info(f"Downloading HuggingFace Bundestag QA '{repo}' (split: {split})...")
        ds = load_dataset(repo, split=split)
        if config.limit is not None and hasattr(ds, "select"):
            ds = ds.select(range(min(len(ds), config.limit)))

        return [self.make_qa_record(row) for row in ds]


class LegalRagBenchLoader(BaseLoaderStrategy):
    """Loader dedicated to the Legal RAG Bench benchmark dataset."""

    REPO = "isaacus/legal-rag-bench"

    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        split = "test"
        subset = "corpus"
        LOGGER.info(
            f"Downloading HuggingFace Legal RAG Bench corpus '{self.REPO}' (subset: {subset}, split: {split})..."
        )
        try:
            hf_data = load_dataset(self.REPO, subset, split=split)
        except Exception:
            hf_data = load_dataset(self.REPO, split=split)

        if config.limit is not None and hasattr(hf_data, "select"):
            hf_data = hf_data.select(range(min(len(hf_data), config.limit)))

        docs_list = []
        for i, row in enumerate(hf_data):
            text = row["text"]
            doc_id = str(row["id"])
            metadata = {"source": self.REPO, "split": split, "doc_id": doc_id}
            metadata["title"] = row["title"]
            metadata["footnotes"] = row["footnotes"]
            docs_list.append(Document(text=text, id_=doc_id, metadata=metadata))

        return docs_list

    @staticmethod
    def make_qa_record(row: dict) -> QARecord:
        """Normalize and construct a benchmark QA record from arbitrary source mappings."""
        return QARecord(
            query_id=str(row["id"]),
            query=str(row["question"]),
            answer=str(row["answer"]),
            doc_id=str(row["relevant_passage_id"]),
        )

    def load_qa(self, config: DataLoaderConfig) -> list[QARecord]:
        split = "test"
        subset = "qa"
        LOGGER.info(
            f"Downloading HuggingFace Legal RAG Bench QA '{self.REPO}' (subset: {subset}, split: {split})..."
        )
        try:
            ds = load_dataset(self.REPO, subset, split=split)
        except Exception:
            ds = load_dataset(self.REPO, split=split)

        if config.limit is not None and hasattr(ds, "select"):
            ds = ds.select(range(min(len(ds), config.limit)))

        return [self.make_qa_record(row) for row in ds]


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

    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        return self._resolve_loader(config).load_corpus(config)

    def load_qa(self, config: DataLoaderConfig) -> list[QARecord]:
        return self._resolve_loader(config).load_qa(config)


class LocalFileLoader(BaseLoaderStrategy):
    """Loader for local JSON and JSONL benchmark files."""

    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        LOGGER.info(f"Loading local file '{config.path_or_repo}'...")
        docs_list = []
        with open(config.path_or_repo, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                item = json.loads(line_str)
                doc_id = str(item.get("id", item.get("doc_id", "")))
                text = item.get("text", item.get("content", ""))
                docs_list.append(
                    Document(
                        text=text,
                        id_=doc_id,
                        metadata={"source": config.path_or_repo, "doc_id": doc_id},
                    )
                )
        return docs_list

    @staticmethod
    def make_qa_record(row: dict) -> QARecord:
        """Normalize and construct a benchmark QA record from arbitrary source mappings."""
        return QARecord(
            query_id=str(row["query_id"]),
            query=str(row["query"]),
            answer=str(row["answer"]),
            doc_id=str(row["doc_id"]),
        )

    def load_qa(self, config: DataLoaderConfig) -> list[QARecord]:
        LOGGER.info(f"Loading local QA file '{config.path_or_repo}'...")
        with open(config.path_or_repo, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()

        if not content:
            return []

        if content.startswith("["):
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    return [self.make_qa_record(r) for r in data]
            except json.JSONDecodeError:
                pass

        rows: list[QARecord] = []
        for line in content.splitlines():
            line_str = line.strip()
            if line_str:
                r = json.loads(line_str)
                rows.append(self.make_qa_record(r))
        return rows


class DirectoryLoader(BaseLoaderStrategy):
    """Loader scanning local directories for documents or QA files."""

    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        LOGGER.info(f"Scanning local directory '{config.path_or_repo}'...")
        return SimpleDirectoryReader(config.path_or_repo).load_data()

    def load_qa(self, config: DataLoaderConfig) -> list[QARecord]:
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
                return LocalFileLoader().load_qa(file_config)

        raise FileNotFoundError(
            f"No QA dataset file (qa.jsonl, queries.jsonl, test.jsonl, etc.) found in directory '{dir_path}'."
        )


class DataLoader:
    """
    Unified Data Loader Facade.
    Applies Separation of Concerns by routing to specific strategies internally.
    """

    def __init__(self):
        self.strategies = {
            DataSourceType.HUGGINGFACE: HuggingFaceLoader(),
            DataSourceType.LOCAL_FILE: LocalFileLoader(),
            DataSourceType.DIRECTORY: DirectoryLoader(),
        }

    def load_corpus(
        self, config_or_target: DataLoaderConfig | str | Path | None = None, **kwargs
    ) -> list[Document]:
        """Main entry point to load corpus documents."""
        if isinstance(config_or_target, DataLoaderConfig):
            config = config_or_target
        elif config_or_target is not None:
            config = DataLoaderConfig(path_or_repo=str(config_or_target), **kwargs)
        else:
            config = DataLoaderConfig(path_or_repo=DataPreset.BUNDESTAG_CORPUS, **kwargs)

        strategy = self.strategies.get(config.source_type)
        if not strategy:
            raise ValueError(f"Unknown source type: {config.source_type}")

        docs = strategy.load_corpus(config)
        LOGGER.info(f"Successfully loaded {len(docs)} corpus documents.")
        return docs

    def load_qa(
        self,
        config_or_target: DataLoaderConfig | str | Path | None = None,
        split: str = "test",
        limit: int | None = None,
        **kwargs,
    ) -> list[QARecord]:
        """Main entry point to load QA pairs."""
        if isinstance(config_or_target, DataLoaderConfig):
            config = config_or_target
        elif config_or_target is not None:
            config = DataLoaderConfig(
                path_or_repo=str(config_or_target), split=split, limit=limit, **kwargs
            )
        else:
            config = DataLoaderConfig(
                path_or_repo=DataPreset.BUNDESTAG_QA, split=split, limit=limit, **kwargs
            )

        strategy = self.strategies.get(config.source_type)
        if not strategy:
            raise ValueError(f"Unknown source type: {config.source_type}")

        qa_pairs = strategy.load_qa(config)
        LOGGER.info(f"Successfully loaded {len(qa_pairs)} QA pairs.")
        return qa_pairs

    @staticmethod
    def to_qrels_df(qa_pairs: list[QARecord]) -> pd.DataFrame:
        """Convert a list of QARecord objects to a PyTerrier-compatible qrels DataFrame.

        Returns DataFrame with columns: ['qid', 'docno', 'label', 'rank', 'query'] where label=1.
        """
        rows: list[dict[str, Any]] = [r.to_pyterrier() for r in qa_pairs]

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

    def load_qrels(
        self,
        config_or_target: DataLoaderConfig | str | Path | None = None,
        split: str = "test",
        limit: int | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """Load QA pairs and return as a PyTerrier qrels DataFrame."""
        qa_pairs = self.load_qa(config_or_target, split=split, limit=limit, **kwargs)
        return self.to_qrels_df(qa_pairs)

    def collate_documents(self, docs: list[Document], join_str: str = "\n\n") -> list[Document]:
        raise NotImplementedError("Not implemented yet.")

    def load_and_save(
        self,
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
        source_type, repo = DataPreset.resolve(name_or_repo)
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
        docs = self.load_corpus(corpus_config)

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
            qa_pairs = self.load_qa(qa_config)
            if qa_pairs:
                qa_file = target_dir / f"qa{suffix}.jsonl"
                with open(qa_file, "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(qa, ensure_ascii=False) + "\n" for qa in qa_pairs)

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
]
