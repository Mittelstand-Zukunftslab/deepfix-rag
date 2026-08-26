"""Build a dense vector index for a corpus dataset."""

import json
from pathlib import Path

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.retrieval.models import EmbeddingClient


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0:
        return [text]
    step = max(size - overlap, 1)
    chunks = []
    for start in range(0, len(text), step):
        chunk = text[start : start + size]
        if chunk.strip():
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks or [text]


def load_corpus(dataset_path: Path) -> list[dict]:
    """Load corpus documents. Expects a JSONL file with 'id'/'doc_id' and 'text'/'content'
    fields, or a HuggingFace dataset preset identifier with a 'corpus' config/split."""
    path = Path(dataset_path)
    if path.is_file():
        docs = []
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                doc_id = str(row.get("id") or row.get("doc_id") or row.get("_id"))
                text = row.get("text") or row.get("content") or row.get("passage") or ""
                docs.append({"doc_id": doc_id, "text": text})
        return docs

    from datasets import load_dataset

    ds = load_dataset(str(dataset_path).replace("\\", "/"), "corpus", split="test")
    docs = []
    for i, row in enumerate(ds):
        row = dict(row)
        doc_id = str(row.get("id") or row.get("doc_id") or row.get("_id") or i)
        text = row.get("text") or row.get("content") or row.get("passage") or ""
        docs.append({"doc_id": doc_id, "text": text})
    return docs


def build_index(dataset_path: Path, config: RAGConfig, index_dir: str, force: bool = False) -> dict:
    """Chunk, embed, and persist a corpus as a dense index at `index_dir`."""
    out_dir = Path(index_dir)
    index_path = out_dir / "index.json"

    if index_path.exists() and not force:
        raise FileExistsError(
            f"Index already exists at '{index_path}'. Use --force to overwrite."
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    docs = load_corpus(dataset_path)

    entries = []
    for doc in docs:
        for chunk_text in _chunk_text(doc["text"], config.chunking.size, config.chunking.overlap):
            entries.append({"doc_id": doc["doc_id"], "text": chunk_text})

    embedder = EmbeddingClient(model=config.embedding.model, provider=config.embedding.provider)
    batch_size = config.embedding.batch_size

    for start in range(0, len(entries), batch_size):
        batch = entries[start : start + batch_size]
        vectors = embedder.embed_documents([e["text"] for e in batch])
        for entry, vector in zip(batch, vectors):
            entry["embedding"] = vector

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(entries, f)

    return {"documents": len(docs), "chunks": len(entries), "index_path": str(index_path)}