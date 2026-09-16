from abc import ABC, abstractmethod
import json
import logging
from pathlib import Path

from datasets import load_dataset
from llama_index.core import SimpleDirectoryReader
from llama_index.core.schema import Document

from .config.schema import DataLoaderConfig, DataSourceType

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

        logger.info(
            f"Downloading HuggingFace dataset '{config.path_or_repo}' (split: {config.split})..."
        )
        hf_data = load_dataset(config.path_or_repo, split=config.split)

        docs_list = []
        for row in hf_data:
            # Safely extract text and ID, falling back to empty string if missing
            text = row.get(config.text_column, row.get("content", ""))
            doc_id = str(row.get(config.doc_id_column, row.get("doc_id", "unnamed_node")))

            # Map standard HF row into native LlamaIndex Document
            docs_list.append(
                Document(text=text, id_=doc_id, metadata={"source": config.path_or_repo})
            )

        return docs_list

    def load_qa(self, config: DataLoaderConfig) -> list[dict]:
        
        path = str(config.path_or_repo).replace("\\", "/")
        config_name = config.config_name or "qa"
        split = config.split or "test"

        logger.info(
            f"Downloading HuggingFace QA dataset '{path}' (subset: {config_name}, split: {split})..."
        )
        try:
            ds = load_dataset(path, config_name, split=split)
        except Exception:
            logger.info(
                f"Subset '{config_name}' not found for '{path}', falling back to default subset."
            )
            ds = load_dataset(path, split=split)

        return [dict(row) for row in ds]


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
