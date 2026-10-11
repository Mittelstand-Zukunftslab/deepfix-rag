# 08 — Incremental per-document indexing

**What to build:** Uploading or deleting one document changes only that document's chunks. Search and Ask keep reading the current index while the change is applied. A failed document does not disturb documents that are already indexed, and retrying a document replaces its own chunks without duplicating them.

**Blocked by:**

- 06 — Full blue-green index build and index status

**Status:** ready-for-agent

- [ ] Uploading one document creates a background job for that document. It does not re-read, re-chunk, or re-embed any other document.
- [ ] The job chunks only that document with the active champion config, preserves the parent document id on every chunk, embeds those chunks, and inserts them into the current index.
- [ ] A document moves pending, then indexing, then indexed. On failure it moves to failed, stores the index error, and leaves all other documents and chunks unchanged. Retrying is idempotent: existing chunks for that document are removed before its replacement chunks are inserted.
- [ ] Deleting a document moves it to deleting, removes every chunk whose parent document id matches, removes the corpus source file, then sets the deleted time. A failed index deletion leaves the document visible as failed or deleting to admins and is retried. It is not reported as successfully deleted.
- [ ] Per-document mutations are serialized through one index-writer queue. Search and Ask continue reading the current index while a mutation runs. After a successful insert or delete, the lexical retriever is refreshed so lexical results include the change.
- [ ] The Library shows each document's indexing status and polls every 3 seconds while any document is pending, indexing, or deleting, or while the index is rebuilding.
- [ ] An incremental failure does not disable Search or Ask for documents already indexed.
- [ ] An integration test indexes documents A and B, uploads document C, and proves A and B keep the same chunk ids and stored embeddings while C becomes retrievable. A second test deletes B and proves only B's chunks disappear.
- [ ] Typecheck passes.
- [ ] Verify in the browser.
