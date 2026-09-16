from abc import ABC, abstractmethod
import json
import logging
from pathlib import Path

from datasets import load_dataset
from llama_index.core import SimpleDirectoryReader
from llama_index.core.schema import Document

from .config.schema import DataLoaderConfig, DataSourceType, DataPreset
from .config.settings import settings

logger = logging.getLogger(__name__)


class BaseLoaderStrategy(ABC):
    """Abstract interface for all data loaders."""

    @abstractmethod
    def load(self, config: DataLoaderConfig) -> list[Document]:
        raise NotImplementedError("Subclasses must implement load()")

    @abstractmethod
    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
        raise NotImplementedError("Subclasses must implement load_qa()")


class HuggingFaceLoader(BaseLoaderStrategy):
    def load(self, config: DataLoaderConfig) -> list[Document]:
        _, repo = DataPreset.resolve(config.path_or_repo)
        split = config.split or "train"
        config_name = config.config_name

        if repo == "isaacus/legal-rag-bench" and not config_name:
            config_name = "corpus"
            split = "test"
        elif repo in ("wirthual/dip-bundestag-qa", "wirthual/dip-bundestag"):
            split = "train"

        logger.info(
            f"Downloading HuggingFace dataset '{repo}' (subset: {config_name}, split: {split})..."
        )
        try:
            if config_name:
                hf_data = load_dataset(repo, config_name, split=split)
            else:
                hf_data = load_dataset(repo, split=split)
        except Exception:
            hf_data = load_dataset(repo, split=split)

        if config.limit is not None and hasattr(hf_data, "select"):
            hf_data = hf_data.select(range(min(len(hf_data), config.limit)))

        docs_list = []
        for i, row in enumerate(hf_data):
            text = (
                row.get(config.text_column)
                or row.get("content")
                or row.get("answer")
                or row.get("question")
                or ""
            )
            doc_id = str(
                row.get(config.doc_id_column)
                or row.get("doc_id")
                or row.get("index")
                or f"doc_{i}"
            )
            docs_list.append(
                Document(text=text, id_=doc_id, metadata={"source": repo, "split": split})
            )

        return docs_list

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
        _, repo = DataPreset.resolve(config.path_or_repo)
        split = config.split or "train"
        config_name = config.config_name

        if repo == "isaacus/legal-rag-bench":
            config_name = config_name or "qa"
            split = "test"
        elif repo == "wirthual/dip-bundestag-qa":
            split = "train"
            config_name = None

        logger.info(
            f"Downloading HuggingFace QA dataset '{repo}' (subset: {config_name}, split: {split})..."
        )
        try:
            if config_name:
                ds = load_dataset(repo, config_name, split=split)
            else:
                ds = load_dataset(repo, split=split)
        except Exception:
            logger.info(
                f"Subset '{config_name}' not found for '{repo}', falling back to default subset."
            )
            ds = load_dataset(repo, split=split)

        if config.limit is not None and hasattr(ds, "select"):
            ds = ds.select(range(min(len(ds), config.limit)))

        qa_list = []
        for i, row in enumerate(ds):
            query_id = str(row.get("query_id") or row.get("index") or row.get("id") or f"q_{i}")
            query = row.get(config.query_column) or row.get("question") or row.get("query") or ""
            answer = row.get(config.answer_column) or row.get("answer") or ""
            doc_id = str(row.get(config.doc_id_column) or row.get("doc_id") or "")
            qa_list.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "answer": answer,
                    "doc_id": doc_id,
                }
            )

        return qa_list


class LocalFileLoader(BaseLoaderStrategy):
    def load(self, config: DataLoaderConfig) -> list[Document]:
        logger.info(f"Loading local file '{config.path_or_repo}'...")
        docs_list = []
        with open(config.path_or_repo, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                item = json.loads(line_str)
                doc_id = str(item.get(config.doc_id_column, item.get("doc_id", "")))
                text = item.get(config.text_column, item.get("content", ""))
                docs_list.append(
                    Document(text=text, id_=doc_id, metadata={"source": config.path_or_repo})
                )
        return docs_list

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
        logger.info(f"Loading local QA file '{config.path_or_repo}'...")
        with open(config.path_or_repo, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()

        if not content:
            return []

        if content.startswith("["):
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    return [dict(row) for row in data]
            except json.JSONDecodeError:
                pass

        rows = []
        for line in content.splitlines():
            line_str = line.strip()
            if line_str:
                rows.append(json.loads(line_str))
        return rows


class DirectoryLoader(BaseLoaderStrategy):
    def load(self, config: DataLoaderConfig) -> list[Document]:
        logger.info(f"Scanning local directory '{config.path_or_repo}'...")
        return SimpleDirectoryReader(config.path_or_repo).load_data()

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:

        dir_path = Path(config.path_or_repo)
        logger.info(f"Scanning local directory '{dir_path}' for QA dataset...")

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

    def load_documents(self, config: DataLoaderConfig) -> list[Document]:
        """Main entry point. Validates type and delegates to the correct strategy."""
        strategy = self.strategies.get(config.source_type)
        if not strategy:
            raise ValueError(f"Unknown source type: {config.source_type}")

        docs = strategy.load(config)
        logger.info(f"Successfully loaded {len(docs)} documents.")
        return docs

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
        """Load QA pairs using the configured strategy."""
        strategy = self.strategies.get(config.source_type)
        if not strategy:
            raise ValueError(f"Unknown source type: {config.source_type}")

        qa_pairs = strategy.load_qa(config)
        logger.info(f"Successfully loaded {len(qa_pairs)} QA pairs.")
        return qa_pairs

    def collate_documents(self, docs: list[Document], join_str: str = "\n\n") -> list[Document]:
        """
        Collates (groups and concatenates) documents possessing the same ID.
        This provides contiguous text blobs optimal for testing various chunking techniques.
        """
        collated_map = {}
        for doc in docs:
            doc_id = doc.id_
            if doc_id not in collated_map:
                collated_map[doc_id] = doc
            else:
                collated_map[doc_id].text += join_str + doc.text

        collated_docs = list(collated_map.values())
        logger.info(
            f"Collated {len(docs)} documents into {len(collated_docs)} contiguous documents."
        )
        return collated_docs

    def load_qa_from_source(
        self,
        path_or_repo: str | Path,
        split: str = "test",
        config_name: str | None = "qa",
        query_column: str = "question",
        answer_column: str = "answer",
    ) -> list[dict]:
        """Automatically detects source type and loads QA dataset."""
        from pathlib import Path

        p = Path(path_or_repo)
        if p.is_file() and p.exists():
            source_type = DataSourceType.LOCAL_FILE
        elif p.is_dir() and p.exists():
            source_type = DataSourceType.DIRECTORY
        else:
            source_type = DataSourceType.HUGGINGFACE

        config = DataLoaderConfig(
            source_type=source_type,
            path_or_repo=str(path_or_repo),
            split=split,
            config_name=config_name,
            query_column=query_column,
            answer_column=answer_column,
        )
        return self.load_qa(config)

    def load_and_save(
        self,
        name_or_repo: str,
        split: str = "train",
        data_root: str | Path | None = None,
        limit: int | None = None,
    ) -> tuple[Path, Path]:
        """
        Loads documents and QA records for a dataset and exports them into standard files:
          <data_root>/<dataset_name>/<split>/corpus.jsonl
          <data_root>/<dataset_name>/<split>/qa.jsonl
        """

        root = Path(data_root or settings.DATA_PROCESSES_DIR)
        source_type, repo = DataPreset.resolve(name_or_repo)
        dataset_name = str(name_or_repo).removeprefix("HF:").split("/")[-1].lower()

        target_dir = root / dataset_name / split
        target_dir.mkdir(parents=True, exist_ok=True)

        config = DataLoaderConfig(
            source_type=source_type,
            path_or_repo=repo,
            split=split,
            limit=limit,
        )

        docs = self.load_documents(config)
        qa_pairs = self.load_qa(config)

        suffix = f"_{limit}" if limit else ""
        corpus_file = target_dir / f"corpus{suffix}.jsonl"
        with open(corpus_file, "w", encoding="utf-8") as f:
            for doc in docs:
                f.write(
                    json.dumps({"doc_id": doc.id_, "text": doc.text}, ensure_ascii=False) + "\n"
                )

        qa_file = target_dir / f"qa{suffix}.jsonl"
        with open(qa_file, "w", encoding="utf-8") as f:
            for qa in qa_pairs:
                f.write(json.dumps(qa, ensure_ascii=False) + "\n")

        return corpus_file, qa_file
