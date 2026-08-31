"""Push local LanceDB tables to remote RustFS / S3 storage."""

import os
from pathlib import Path
from dotenv import load_dotenv
import lancedb
import pandas as pd

load_dotenv()


def get_storage_options():
    return {
        "endpoint": os.getenv("RUSTFS_ENDPOINT", "https://hpi-s3.mittelstand-zukunftslab.de"),
        "aws_access_key_id": os.getenv("RUSTFS_ACCESS_KEY", "rustfsadmin"),
        "aws_secret_access_key": os.getenv("RUSTFS_SECRET_KEY", ""),
        "aws_region": "us-east-1",
        "allow_http": "true",
        "aws_virtual_hosted_style_request": "false",
    }


def push_all_to_rustfs():
    storage_options = get_storage_options()
    print(f"RustFS endpoint: {storage_options['endpoint']}")
    print(f"Access key: {storage_options['aws_access_key_id']}")

    # 1. Load legal passages (4,876 benchmark rows)
    df_legal = None
    if Path("data/lancedb").exists():
        ldb = lancedb.connect("data/lancedb")
        tables = ldb.table_names() if hasattr(ldb, "table_names") else ldb.list_tables()
        if "legal_passages" in tables:
            df_legal = ldb.open_table("legal_passages").to_pandas()
            print(f"Loaded {len(df_legal)} legal passages from data/lancedb.")

    # 2. Load documents (15,282 rows from real_index)
    df_docs = None
    if Path("real_index").exists():
        rdb = lancedb.connect("real_index")
        tables = rdb.table_names() if hasattr(rdb, "table_names") else rdb.list_tables()
        if "documents" in tables:
            df_docs = rdb.open_table("documents").to_pandas()
            print(f"Loaded {len(df_docs)} documents from real_index.")

    targets = [
        "s3://deepfix-index/legal_rag_bench",
        "s3://deepfix-index/real_index",
        "s3://deepfix-index",
    ]

    for target_uri in targets:
        print(f"\n--- Pushing to {target_uri} ---")
        try:
            db = lancedb.connect(target_uri, storage_options=storage_options)

            if df_legal is not None:
                print(f"Creating 'legal_passages' ({len(df_legal)} rows) in {target_uri}...")
                tbl = db.create_table("legal_passages", data=df_legal, mode="overwrite")
                try:
                    from lancedb.index import FTS
                    tbl.create_index("text", config=FTS())
                except Exception:
                    pass
                print(f" Table 'legal_passages' created with {tbl.count_rows()} rows.")

            if df_docs is not None:
                print(f"Creating 'documents' ({len(df_docs)} rows) in {target_uri}...")
                tbl_docs = db.create_table("documents", data=df_docs, mode="overwrite")
                try:
                    from lancedb.index import FTS
                    tbl_docs.create_index("text", config=FTS())
                except Exception:
                    pass
                print(f" Table 'documents' created with {tbl_docs.count_rows()} rows.")

        except Exception as e:
            print(f"Error pushing to {target_uri}: {e}")

    print("\nAll RustFS sync operations completed!")


if __name__ == "__main__":
    push_all_to_rustfs()
