import time
import httpx
from llama_index.core import StorageContext, load_index_from_storage, Settings
from llama_index.embeddings.litellm import LiteLLMEmbedding

# --- CONFIG ---
LITELLM_URL = "https://litellm.mittelstand-zukunftslab.de/"
LITELLM_KEY = "sk-pfrgLhvL2Idn3HCWC0yPAQ"

QDRANT_URL = "https://qdrant.mittelstand-zukunftslab.de"
QDRANT_KEY = "zeuhzyfpmfyp7i71opfbglk9cwyg7uhd"

COLLECTION_NAME = "agrim_legal_bench_v1"
SAVE_PATH = "./data/processed/agrim_legal_bench_index"
BATCH_SIZE = 50
MAX_RETRIES = 8
RETRY_DELAY = 5

Settings.embed_model = LiteLLMEmbedding(
    model_name="openai/qwen/qwen3-embedding-8b",
    api_key=LITELLM_KEY,
    api_base=LITELLM_URL
)
Settings.llm = None

print("1. Loading locally saved Qwen embeddings from your hard drive...")
local_storage_context = StorageContext.from_defaults(persist_dir=SAVE_PATH)
local_index = load_index_from_storage(local_storage_context)
nodes = list(local_index.docstore.docs.values())
print(f"Loaded {len(nodes)} pre-computed chunks!")

# Find the embedding vectors -- llama-index stores these in the vector store,
# not in the docstore nodes themselves.
vector_store = local_storage_context.vector_store
embedding_dict = None
for attr_path in ["data.embedding_dict", "_data.embedding_dict"]:
    obj = vector_store
    ok = True
    for part in attr_path.split("."):
        if hasattr(obj, part):
            obj = getattr(obj, part)
        else:
            ok = False
            break
    if ok:
        embedding_dict = obj
        break

if embedding_dict is None:
    raise RuntimeError(
        "Could not locate embedding_dict on vector_store. "
        f"Available attrs: {dir(vector_store)}"
    )

print(f"Found {len(embedding_dict)} embeddings in vector store.")

session = httpx.Client(
    base_url=QDRANT_URL,
    headers={"api-key": QDRANT_KEY, "Content-Type": "application/json"},
    timeout=60
)

# --- 2. Check connectivity / collection exists ---
print("\n2. Checking Qdrant collection...")
r = session.get(f"/collections/{COLLECTION_NAME}")
print(f"   Status: {r.status_code}")
if r.status_code != 200:
    raise RuntimeError(f"Collection check failed: {r.text}")
print("   Collection found!")

# --- 3. Upload points directly via REST, batch by batch ---
print(f"\n3. Uploading {len(nodes)} points in batches of {BATCH_SIZE}...")

point_id_counter = 0
success_count = 0

for i in range(0, len(nodes), BATCH_SIZE):
    batch = nodes[i: i + BATCH_SIZE]
    points = []
    for node in batch:
        node_id = node.node_id
        embedding = embedding_dict.get(node_id)
        if embedding is None:
            print(f"    Skipping node {node_id} -- no embedding found.")
            continue
        payload = {
            "text": node.get_content(),
            **(node.metadata or {})
        }
        points.append({
            "id": point_id_counter,
            "vector": embedding,
            "payload": payload
        })
        point_id_counter += 1

    if not points:
        continue

    label = f"{i}-{i + len(batch)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.put(
                f"/collections/{COLLECTION_NAME}/points",
                json={"points": points},
                params={"wait": "true"}
            )
            if resp.status_code in (200, 202):
                print(f" -> Batch {label}: SUCCESS ({len(points)} points)")
                success_count += len(points)
                break
            else:
                print(f" -> Batch {label} attempt {attempt} failed: {resp.status_code} {resp.text[:200]}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f" -> Batch {label} attempt {attempt} exception: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    time.sleep(0.3)

print(f"\nDONE! Uploaded {success_count} / {len(nodes)} points to Qdrant.")
