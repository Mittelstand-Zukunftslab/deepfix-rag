"""
Convert real_index/index.json into LanceDB tables ('documents' and 'legal_passages').
"""

import json
from pathlib import Path
import lancedb
import pandas as pd


def convert_real_index():
    index_json_path = Path("real_index/index.json")
    if not index_json_path.exists():
        print(f"File not found: {index_json_path}")
        return

    print(f"Loading {index_json_path}...")
    with open(index_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"Loaded {len(data)} entries. Formatting records...")
    records = []
    for item in data:
        doc_id = str(item.get("doc_id") or item.get("id") or "")
        records.append({
            "id": doc_id,
            "doc_id": doc_id,
            "text": item.get("text", ""),
            "vector": item.get("embedding", []),
        })

    df = pd.DataFrame(records)
    print(f"Connecting to LanceDB at 'real_index'...")
    db = lancedb.connect("real_index")

    # Create 'documents' table
    print("Creating 'documents' table in real_index...")
    table_docs = db.create_table("documents", data=df, mode="overwrite")
    print(f"Table 'documents' created with {table_docs.count_rows()} rows.")

    # Create 'legal_passages' table alias as well
    print("Creating 'legal_passages' table in real_index...")
    table_passages = db.create_table("legal_passages", data=df, mode="overwrite")
    print(f"Table 'legal_passages' created with {table_passages.count_rows()} rows.")

    # Also build FTS index on 'text'
    try:
        from lancedb.index import FTS
        print("Building FTS index on documents.text...")
        table_docs.create_index("text", config=FTS())
        print("FTS index built on 'documents'.")
    except Exception as e:
        print(f"FTS index note on 'documents': {e}")

    # Also update data/lancedb to have 'documents' table
    print("\nAlso syncing to data/lancedb/documents...")
    db_data = lancedb.connect("data/lancedb")
    tbl_data = db_data.create_table("documents", data=df, mode="overwrite")
    try:
        from lancedb.index import FTS
        tbl_data.create_index("text", config=FTS())
    except Exception as e:
        print(f"FTS index note on data/lancedb: {e}")
    print(f"Synced {tbl_data.count_rows()} rows to data/lancedb/documents.")

    print("\nAll LanceDB conversions completed successfully!")


if __name__ == "__main__":
    convert_real_index()
