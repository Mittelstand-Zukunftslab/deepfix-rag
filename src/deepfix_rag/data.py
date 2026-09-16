import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, List, Optional, Dict

from llama_index.core.schema import Document
from llama_index.core import SimpleDirectoryReader
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DataSourceType(StrEnum):
    HUGGINGFACE = "huggingface"
    LOCAL_FILE = "local_file"
    DIRECTORY = "directory"


class DataLoaderConfig(BaseModel):
    """Configuration for data loading requests."""
    source_type: DataSourceType = DataSourceType.LOCAL_FILE
    path_or_repo: str = Field(..., description="Local path or HuggingFace repo identifier.")
    split: Optional[str] = Field("train", description="Dataset split (for HF sources).")
    subset: Optional[str] = Field(None, description="Dataset config/subset name (for HF sources).")
    text_column: str = Field("text", description="Column or key containing the document text.")
    doc_id_column: str = Field("id", description="Column or key containing document ID.")


class BaseLoaderStrategy:
    """Abstract interface for all data loader strategies."""
    def load(self, config: DataLoaderConfig) -> List[Document]:
        raise NotImplementedError("Subclasses must implement load()")


class HuggingFaceLoader(BaseLoaderStrategy):
    """Loads datasets directly from Hugging Face Hub."""
    def load(self, config: DataLoaderConfig) -> List[Document]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise ImportError("The 'datasets' library is required to load from HuggingFace. Run 'pip install datasets'.")

        logger.info(f"Loading HuggingFace dataset '{config.path_or_repo}' (split: {config.split}, subset: {config.subset})...")
        
        args = [config.path_or_repo]
        if config.subset:
            args.append(config.subset)
            
        kwargs = {}
        if config.split:
            kwargs["split"] = config.split

        hf_dataset = load_dataset(*args, **kwargs)

        docs = []
        for i, row in enumerate(hf_dataset):
            row_dict = dict(row)
            doc_id = str(row_dict.get(config.doc_id_column) or row_dict.get("doc_id") or row_dict.get("id") or i)
            text = (
                row_dict.get(config.text_column)
                or row_dict.get("content")
                or row_dict.get("passage")
                or row_dict.get("text")
                or ""
            )
            metadata = {k: v for k, v in row_dict.items() if k not in (config.text_column, "content", "passage", "text")}
            metadata["source"] = config.path_or_repo
            metadata["doc_id"] = doc_id

            docs.append(Document(text=text, doc_id=doc_id, metadata=metadata))

        return docs


class LocalFileLoader(BaseLoaderStrategy):
    """Loads documents from a single JSONL or text file."""
    def load(self, config: DataLoaderConfig) -> List[Document]:
        path = Path(config.path_or_repo)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        logger.info(f"Loading local file: {path}")
        docs = []
        with open(path, "r", encoding="utf-8-sig") as f:
            for i, line in enumerate(f):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    item = json.loads(line_str)
                    doc_id = str(item.get(config.doc_id_column) or item.get("doc_id") or item.get("id") or i)
                    text = item.get(config.text_column) or item.get("content") or item.get("text") or ""
                    metadata = {k: v for k, v in item.items() if k not in (config.text_column, "content", "text")}
                    metadata["doc_id"] = doc_id
                    docs.append(Document(text=text, doc_id=doc_id, metadata=metadata))
                except json.JSONDecodeError:
                    docs.append(Document(text=line_str, doc_id=f"line_{i}", metadata={"doc_id": f"line_{i}"}))
        return docs


class DirectoryLoader(BaseLoaderStrategy):
    """Loads documents from a local directory using SimpleDirectoryReader."""
    def load(self, config: DataLoaderConfig) -> List[Document]:
        path = Path(config.path_or_repo)
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")

        logger.info(f"Scanning directory: {path}")
        return SimpleDirectoryReader(str(path)).load_data()


class DataLoader:
    """
    Unified Data Loader interface supporting both legacy (dirname, data_file)
    and polymorphic DataLoaderConfig requests.
    """
    def __init__(self, config: Optional[Any] = None):
        self.config = config
        self._strategies: Dict[DataSourceType, BaseLoaderStrategy] = {
            DataSourceType.HUGGINGFACE: HuggingFaceLoader(),
            DataSourceType.LOCAL_FILE: LocalFileLoader(),
            DataSourceType.DIRECTORY: DirectoryLoader(),
        }

    def load_documents_from_file(self, data_file: str) -> List[Document]:
        """Convenience method for loading from a JSONL file."""
        return self._strategies[DataSourceType.LOCAL_FILE].load(
            DataLoaderConfig(
                source_type=DataSourceType.LOCAL_FILE,
                path_or_repo=data_file,
            )
        )

    def load_documents(
        self,
        config_or_dirname: Optional[Any] = None,
        data_file: Optional[str] = None,
    ) -> List[Document]:
        """
        Unified loading entry point. Supports:
        1. DataLoaderConfig instance
        2. dirname (str)
        3. data_file (str)
        """
        if isinstance(config_or_dirname, DataLoaderConfig):
            strategy = self._strategies.get(config_or_dirname.source_type)
            if not strategy:
                raise ValueError(f"Unsupported DataSourceType: {config_or_dirname.source_type}")
            return strategy.load(config_or_dirname)

        dirname = config_or_dirname if isinstance(config_or_dirname, str) else None

        if dirname:
            return self._strategies[DataSourceType.DIRECTORY].load(
                DataLoaderConfig(
                    source_type=DataSourceType.DIRECTORY,
                    path_or_repo=dirname,
                )
            )
        elif data_file:
            return self.load_documents_from_file(data_file)
        else:
            raise ValueError("Either a DataLoaderConfig, dirname, or data_file must be provided.")
