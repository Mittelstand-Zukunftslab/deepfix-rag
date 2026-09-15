import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional

from llama_index.core.schema import Document
from llama_index.core import SimpleDirectoryReader
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# 1. ENUMS & CONFIGURATION (Type Safety & Pydantic)
# -------------------------------------------------------------------------

class DataSourceType(str, Enum):
    HUGGINGFACE = "huggingface"
    LOCAL_FILE = "local_file"
    DIRECTORY = "directory"

class DataLoaderConfig(BaseModel):
    """Pydantic model to strictly validate data loading requests."""
    source_type: DataSourceType
    path_or_repo: str = Field(..., description="Local path or HuggingFace repo ID.")
    split: Optional[str] = Field("train", description="Dataset split (HF only).")
    text_column: str = Field("text", description="Which column to map to the Document body.")
    doc_id_column: str = Field("id", description="Which column to map to the Document ID.")

# -------------------------------------------------------------------------
# 2. THE STRATEGY PATTERN (Loaders)
# -------------------------------------------------------------------------

class BaseLoaderStrategy:
    """Abstract interface for all data loaders."""
    def load(self, config: DataLoaderConfig) -> List[Document]:
        raise NotImplementedError("Subclasses must implement load()")

class HuggingFaceLoader(BaseLoaderStrategy):
    def load(self, config: DataLoaderConfig) -> List[Document]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise ImportError("Please install the 'datasets' library to load from HuggingFace.")
        
        logger.info(f"Downloading HuggingFace dataset '{config.path_or_repo}' (split: {config.split})...")
        hf_data = load_dataset(config.path_or_repo, split=config.split)
        
        docs_list = []
        for row in hf_data:
            # Safely extract text and ID, falling back to empty string if missing
            text = row.get(config.text_column, row.get("content", ""))
            doc_id = str(row.get(config.doc_id_column, row.get("doc_id", "unnamed_node")))
            
            # Map standard HF row into native LlamaIndex Document
            docs_list.append(Document(text=text, id_=doc_id, metadata={"source": config.path_or_repo}))
            
        return docs_list

class LocalFileLoader(BaseLoaderStrategy):
    def load(self, config: DataLoaderConfig) -> List[Document]:
        logger.info(f"Loading local file '{config.path_or_repo}'...")
        docs_list = []
        with open(config.path_or_repo, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    item = json.loads(line_str)
                    doc_id = str(item.get(config.doc_id_column, item.get("doc_id", "")))
                    text = item.get(config.text_column, item.get("content", ""))
                    docs_list.append(Document(text=text, id_=doc_id, metadata={"source": config.path_or_repo}))
        return docs_list

class DirectoryLoader(BaseLoaderStrategy):
    def load(self, config: DataLoaderConfig) -> List[Document]:
        logger.info(f"Scanning local directory '{config.path_or_repo}'...")
        return SimpleDirectoryReader(config.path_or_repo).load_data()

# -------------------------------------------------------------------------
# 3. THE FACTORY (Single point of entry)
# -------------------------------------------------------------------------

class DataLoader:
    """
    Unified Data Loader Facade. 
    Applies Separation of Concerns by routing to specific strategies internally.
    """
    def __init__(self):
        # Register our specific loading strategies
        self.strategies = {
            DataSourceType.HUGGINGFACE: HuggingFaceLoader(),
            DataSourceType.LOCAL_FILE: LocalFileLoader(),
            DataSourceType.DIRECTORY: DirectoryLoader(),
        }

    def load_documents(self, config: DataLoaderConfig) -> List[Document]:
        """Main entry point. Validates type and delegates to the correct strategy."""
        strategy = self.strategies.get(config.source_type)
        if not strategy:
            raise ValueError(f"Unknown source type: {config.source_type}")
        
        docs = strategy.load(config)
        logger.info(f"Successfully loaded {len(docs)} documents.")
        return docs
