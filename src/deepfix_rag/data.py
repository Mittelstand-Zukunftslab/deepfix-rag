"""Data loading and ingestion strategies for RAG."""

from abc import ABC, abstractmethod
import json
from pathlib import Path

from datasets import load_dataset
from llama_index.core import SimpleDirectoryReader
from llama_index.core.schema import Document

from .config.schema import DataLoaderConfig, DataPreset, DataSourceType
from .config.settings import LOGGER, settings


class BaseLoaderStrategy(ABC):
    """Abstract interface for all data loaders."""

    @abstractmethod
    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        """Load and return corpus documents."""
        raise NotImplementedError("Subclasses must implement load_corpus()")

    @abstractmethod
    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
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
            text = row.get("text") or row.get("content") or ""
            if not text:
                continue
            doc_id = str(row.get("id") or row.get("doc_id") or row.get("index") or f"doc_{i}")
            metadata = {"source": repo, "split": split}
            if row.get("title"):
                metadata["title"] = row["title"]
            if row.get("footnotes"):
                metadata["footnotes"] = row["footnotes"]
            docs_list.append(Document(text=text, id_=doc_id, metadata=metadata))

        return docs_list

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
        repo = self.QA_REPO
        split = config.split or "train"
        LOGGER.info(f"Downloading HuggingFace Bundestag QA '{repo}' (split: {split})...")
        ds = load_dataset(repo, split=split)
        if config.limit is not None and hasattr(ds, "select"):
            ds = ds.select(range(min(len(ds), config.limit)))

        qa_list = []
        for i, row in enumerate(ds):
            query_id = str(row.get("query_id") or row.get("index") or row.get("id") or f"q_{i}")
            query = row.get("question") or row.get("query") or ""
            answer = row.get("answer") or ""
            doc_id = str(row.get("doc_id") or "")
            qa_list.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "answer": answer,
                    "doc_id": doc_id,
                }
            )
        return qa_list


class LegalRagBenchLoader(BaseLoaderStrategy):
    """Loader dedicated to the Legal RAG Bench benchmark dataset."""

    REPO = "isaacus/legal-rag-bench"

    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        split = config.split if (config.split and config.split != "train") else "test"
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
            text = row.get("text") or row.get("content") or ""
            if not text:
                continue
            doc_id = str(row.get("id") or row.get("doc_id") or row.get("index") or f"doc_{i}")
            metadata = {"source": self.REPO, "split": split}
            if row.get("title"):
                metadata["title"] = row["title"]
            if row.get("footnotes"):
                metadata["footnotes"] = row["footnotes"]
            docs_list.append(Document(text=text, id_=doc_id, metadata=metadata))

        return docs_list

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
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

        qa_list = []
        for i, row in enumerate(ds):
            query_id = str(row.get("query_id") or row.get("index") or row.get("id") or f"q_{i}")
            query = row.get("question") or row.get("query") or ""
            answer = row.get("answer") or ""
            doc_id = str(row.get("doc_id") or "")
            qa_list.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "answer": answer,
                    "doc_id": doc_id,
                }
            )
        return qa_list


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

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
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
                    Document(text=text, id_=doc_id, metadata={"source": config.path_or_repo})
                )
        return docs_list

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
        LOGGER.info(f"Loading local QA file '{config.path_or_repo}'...")
        with open(config.path_or_repo, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()

        if not content:
            return []

        if content.startswith("["):
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    return [
                        {
                            "query_id": str(r.get("query_id") or r.get("id") or f"q_{i}"),
                            "query": r.get("query") or r.get("question") or "",
                            "answer": r.get("answer") or "",
                            "doc_id": str(r.get("doc_id") or ""),
                        }
                        for i, r in enumerate(data)
                    ]
            except json.JSONDecodeError:
                pass

        rows = []
        for i, line in enumerate(content.splitlines()):
            line_str = line.strip()
            if line_str:
                r = json.loads(line_str)
                rows.append(
                    {
                        "query_id": str(r.get("query_id") or r.get("id") or f"q_{i}"),
                        "query": r.get("query") or r.get("question") or "",
                        "answer": r.get("answer") or "",
                        "doc_id": str(r.get("doc_id") or ""),
                    }
                )
        return rows


class DirectoryLoader(BaseLoaderStrategy):
    """Loader scanning local directories for documents or QA files."""

    def load_corpus(self, config: DataLoaderConfig) -> list[Document]:
        LOGGER.info(f"Scanning local directory '{config.path_or_repo}'...")
        return SimpleDirectoryReader(config.path_or_repo).load_data()

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
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

    def load_corpus(self, config_or_target: DataLoaderConfig | str | Path | None = None, **kwargs) -> list[Document]:
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
    ) -> list[dict]:
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
            if (repo in (BundestagLoader.CORPUS_REPO, BundestagLoader.QA_REPO) or "bundestag" in dataset_name)
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
                if (repo in (BundestagLoader.CORPUS_REPO, BundestagLoader.QA_REPO) or "bundestag" in dataset_name)
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
]
