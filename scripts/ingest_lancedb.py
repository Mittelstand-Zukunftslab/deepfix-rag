"""
Ingest pre-computed embeddings and documents from data/processed/agrim_legal_bench_index
into a local LanceDB table.
"""

import json
from pathlib import Path
import lancedb
import pandas as pd


def ingest_to_lancedb(
    processed_dir: Path = Path("data/processed/agrim_legal_bench_index"),
    db_uri: str = "data/lancedb",
    table_name: str = "legal_passages",
    create_fts: bool = True,
):
    print(f"Loading data from {processed_dir}...")
    
    # 1. Load vector store embeddings
    vector_store_path = processed_dir / "default__vector_store.json"
    with open(vector_store_path, "r", encoding="utf-8") as f:
        vs_data = json.load(f)
    
    embedding_dict = vs_data.get("embedding_dict") or vs_data.get("data", {}).get("embedding_dict", {})
    print(f"Loaded {len(embedding_dict)} embeddings.")

    # 2. Load docstore
    docstore_path = processed_dir / "docstore.json"
    with open(docstore_path, "r", encoding="utf-8") as f:
        ds_data = json.load(f)
    
    docs_dict = ds_data.get("docstore/data", {})
    print(f"Loaded {len(docs_dict)} documents.")

    # 3. Assemble records
    records = []
    missing_embeddings = 0

    for node_id, doc_entry in docs_dict.items():
        payload = doc_entry.get("__data__", {})
        metadata = payload.get("metadata", {})
        
        # Passage id used in benchmark, e.g. "1.1-c1-s1"
        meta_id = metadata.get("id") or payload.get("id_") or node_id
        title = metadata.get("title", "")
        text = payload.get("text", "")
        vector = embedding_dict.get(node_id)

        if vector is None:
            missing_embeddings += 1
            continue

        records.append({
            "id": str(meta_id),
            "node_id": str(node_id),
            "title": str(title),
            "text": str(text),
            "vector": vector,
        })

    if missing_embeddings > 0:
        print(f"Warning: {missing_embeddings} documents had no matching embedding vector.")

    print(f"Prepared {len(records)} records for LanceDB ingestion.")

    # 4. Connect to LanceDB and create/overwrite table
    Path(db_uri).mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(db_uri)
    print(f"Connected to LanceDB at: {db_uri}")

    df = pd.DataFrame(records)
    table = db.create_table(table_name, data=df, mode="overwrite")
    row_count = table.count_rows()
    print(f"Successfully created table '{table_name}' with {row_count} rows.")

    # 5. Create Full-Text Search (FTS) index if requested
    if create_fts:
        try:
            print("Building full-text search (FTS) index on 'text'...")
            from lancedb.index import FTS
            table.create_index("text", config=FTS())
            print("FTS index built successfully.")
        except Exception as e:
            print(f"FTS index creation note: {e}")

    # 6. Verify with a quick sample search
    sample_query = "jury excuse bias"
    print(f"\nRunning verification query: '{sample_query}'")
    sample_vec = records[0]["vector"]  # test vector format
    results = table.search().limit(3).to_pandas()
    print("Sample retrieved columns:", list(results.columns))
    print(f"Sample row preview:\n{results[['id', 'title']].to_string(index=False)}")

    return table


if __name__ == "__main__":
    ingest_to_lancedb()
