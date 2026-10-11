# 06 — Full blue-green index build and index status

**What to build:** The shared corpus gets a serving index without ever writing a build directly into the directory that answers requests. A full build runs when there is no current index, when an index-time configuration key changes, or when an admin asks for a repair. On success the API switches the serving pointer and reloads the query service; requests already in flight finish on the old index. On failure the old index and config keep serving. The Library shows the overall index status, and Search and Ask explain when no serving index exists yet. The workspace corpus directory becomes loadable by the `deepfix` CLI, so the agent can build candidate indexes from it.

**Reference:** tasks/prd-product-ui.md, `### US-008: Maintain the shared index`

**Blocked by:**

- 01 — Query service in the domain layer
- 05 — Upload into the shared corpus and Library screen

**Status:** ready-for-agent

- [ ] The local file loader accepts a directory as the data path and loads every `*.json` file in it as one document. `deepfix index` and `deepfix eval` pointed at the workspace corpus load it. JSONL files keep loading as today.
- [ ] The API owns the location fields of every workspace config: when it loads, builds, or promotes a config, it overwrites the index directory with the serving or build directory and the data path with the workspace corpus. Values a candidate config carries for those fields are ignored.
- [ ] A full rebuild builds all non-deleted documents from the workspace corpus into a new build directory. It never writes into the serving directory.
- [ ] A full rebuild occurs only when there is no current index, when an index-time key changes, or when an admin explicitly requests repair. Index-time keys are `chunking.*`, `retrieval.backend`, `retrieval.vector_store`, and `retrieval.bit_width`. The embedding model is a server setting, not a config key.
- [ ] On success, the API atomically switches the current-index pointer and reloads the query service. Requests already in flight finish on the old index. On failure, the old index and config keep serving. Superseded builds are deleted once no request uses them.
- [ ] An admin can start a repair rebuild. Members receive 403, and a second request while rebuilding receives 409. The Library shows an admin-only "Rebuild index" action with a confirmation explaining that the current index remains available.
- [ ] Index status returns `empty`, `updating`, `rebuilding`, `ready`, `ready_with_errors`, or `failed`, plus document count, chunk count, pending document count, updated time, config path, serving build id, and the most recent error. An individual document failure produces `ready_with_errors` when a serving index still exists. `failed` means no usable index exists.
- [ ] Workspace indexing supports dense, hybrid, and bm25 retrieval only. A champion config with graph or mixed is rejected.
- [ ] The Library shows the overall index status. When no serving index exists, Search and Ask show "The index is being prepared. Try again in a minute."
- [ ] Typecheck passes.
- [ ] Verify in the browser.
