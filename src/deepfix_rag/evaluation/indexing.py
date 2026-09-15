"""Build a LanceDB vector index for a corpus dataset using LlamaIndex standards."""

import json
from pathlib import Path

import lancedb
from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.lancedb import LanceDBVectorStore

from deepfix_rag.config.schema import RAGConfig
from llama_index.embeddings.openai import OpenAIEmbedding


def load_corpus(dataset_path: Path) -> list[Document]:
    """Load corpus documents and convert them to standard LlamaIndex Documents."""
    path = Path(dataset_path)
    docs = []
    if path.is_file():
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                doc_id = str(row.get("id") or row.get("doc_id") or row.get("_id"))
                text = row.get("text") or row.get("content") or row.get("passage") or ""
                docs.append(Document(text=text, doc_id=doc_id))
        return docs

    from datasets import load_dataset

    ds = load_dataset(str(dataset_path).replace("\\", "/"), "corpus", split="test")
    for i, row in enumerate(ds):
        row = dict(row)
        doc_id = str(row.get("id") or row.get("doc_id") or row.get("_id") or i)
        text = row.get("text") or row.get("content") or row.get("passage") or ""
        docs.append(Document(text=text, doc_id=doc_id))
    return docs


def build_index(dataset_path: Path, config: RAGConfig, index_dir: str, force: bool = False) -> dict:
    """Chunk, embed, and persist a corpus as a LanceDB index using LlamaIndex VectorStoreIndex."""
    out_dir = Path(index_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    table_name = "documents"

    mode = "overwrite" if force else "create"
        
    db = lancedb.connect(str(out_dir))
    try:
        tables = db.list_tables()
    except Exception:
        tables = []
        
    if table_name in tables:
        if not force:
            raise FileExistsError(
                f"LanceDB table '{table_name}' already exists at '{out_dir}'. Use --force to overwrite."
            )
        mode = "overwrite"
    
    # 1. Prepare Storage and Node Parsing
    vector_store = LanceDBVectorStore(mode=mode, uri=str(out_dir), table_name=table_name)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    
    node_parser = SentenceSplitter(
        chunk_size=config.chunking.size if config.chunking.size > 0 else 1024,
        chunk_overlap=config.chunking.overlap if config.chunking.overlap > 0 else 0
    )
    
    # 2. Extract Custom Embedding Engine
    embed_model = OpenAIEmbedding(api_base=config.embedding.provider, model_name=config.embedding.model, provider=config.embedding.provider)

    # 3. Load Documents
    documents = load_corpus(dataset_path)

    # 4. Standard LlamaIndex Ingestion
    index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
        transformations=[node_parser],
        embed_model=embed_model,
        show_progress=True
    )
    
    # Optional legacy fallback tracking for old notebooks if they check counts
    return {
        "documents": len(documents),
        "chunks": getattr(vector_store, "_table", None).count_rows() if hasattr(vector_store, "_table") else 0,
        "table_name": table_name,
        "index_path": str(out_dir),
    }
