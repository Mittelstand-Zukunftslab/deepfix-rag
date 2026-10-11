# 05 — Upload into the shared corpus and Library screen

**What to build:** A signed-in member uploads a text, Markdown, or PDF file into the one shared workspace corpus, sees it in the Library, and deletes their own files. Admins can delete any file. Deletion is soft, so later votes can still resolve to a filename. This ticket also lays out the workspace directory (corpus, indexes, feedback, configs, and runs) and the API-private directory, and seeds the champion config from the default config on first start. Only API code writes the champion config. The Library replaces the mock "Data upload" screen.

**Reference:** tasks/prd-product-ui.md, `### US-007` and `### US-013`

**Blocked by:**

- 04 — Accounts and roles

**Status:** ready-for-agent

- [ ] Upload accepts one file. Allowed extensions are `.txt`, `.md`, and `.pdf`. Others return 400.
- [ ] Maximum upload size is 20 MB. Larger files return 400 with a message that states the limit.
- [ ] Text and Markdown are decoded as UTF-8. PDF text is extracted with `pypdf`. A PDF with no extractable text is stored with status failed and reason "No extractable text. Scanned PDFs are not supported."
- [ ] Each document gets an id of the form `ws_` plus a UUID hex. The id never contains `::`, so parent-document resolution returns it unchanged.
- [ ] Each document record stores the document id, original filename, media type, extracted text, status (`pending`, `indexing`, `indexed`, `deleting`, `failed`), index error, uploader, created time, indexed time, and deleted time.
- [ ] The document list returns non-deleted documents for any signed-in user, newest first.
- [ ] The uploader or an admin can delete a document. Other members receive 403. Deletion sets the deleted time so existing votes still resolve to a filename.
- [ ] Before any later indexing, the extracted source is written into the workspace corpus as `<doc_id>.json` with document id, text, and filename. The corpus directory is the reproducible source for a later full rebuild.
- [ ] The workspace contains corpus, indexes, feedback, configs, and runs. The product database lives outside the workspace in an API-private directory.
- [ ] The champion config is seeded from the default config on first start. The API overwrites its index location with the workspace serving directory and its data location with the workspace corpus, so the seeded config never points at S3 or a Hugging Face dataset. Only API code writes it.
- [ ] The Library screen lists filename, uploader email, status, and upload time, and offers Upload and, where allowed, Delete. The mock "Data upload" and "Data visualization" screens and their mock services are removed.
- [ ] Empty state: "No documents yet. Upload a text, Markdown, or PDF file to start."
- [ ] Typecheck passes.
- [ ] Verify in the browser.
