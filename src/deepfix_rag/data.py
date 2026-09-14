
import json
import logging

from llama_index.core.schema import Document
from llama_index.core import SimpleDirectoryReader


logger = logging.getLogger(__name__)

class DataLoader:

    def __init__(self, config):
        self.config = config
    
    def load_documents_from_file(self, data_file: str) -> list[Document]:
        """Read a JSONL file and convert each line into a Document."""
        docs_list = []
        with open(data_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    item = json.loads(line_str)
                    doc_id = str(item.get("doc_id", item.get("id", "")))
                    text = item.get("text", item.get("content", ""))
                    docs_list.append(Document(text=text, doc_id=doc_id, metadata={"doc_id": doc_id}))
        return docs_list

    def load_documents(self, dirname: str | None = None, data_file: str | None = None) -> list[Document]:
        if dirname:
            documents = SimpleDirectoryReader(dirname).load_data()
            logger.info(f"Loaded {len(documents)} documents from {dirname}")
        else:
            if not data_file:
                raise ValueError("data_file is required when dirname is not provided.")
            documents = self.load_documents_from_file(data_file)
            logger.info(f"Loaded {len(documents)} documents from {data_file}")
        return documents