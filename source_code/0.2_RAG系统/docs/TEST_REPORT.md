# ProjectRAG V0.1 Test Report

## Automated tests

Result: **4 passed**.

Covered:

1. external project sync;
2. Markdown document upload;
3. automatic parse/chunk/index;
4. SHA-256 duplicate detection;
5. metadata-filtered hybrid retrieval;
6. dashboard/search/health page responses;
7. synthetic MinerU `content_list.json` parsing with `text_level` heading and `page_idx` page preservation;
8. fake MinerU executable integration, verifying CLI invocation and automatic discovery of Markdown + `content_list.json` outputs.

## Expected behavior without MinerU installed

- Markdown/TXT/HTML: indexes directly;
- PDF/image/DOCX/PPTX/XLSX: persists original and sets status to `WAITING_MINERU`;
- after MinerU installation, re-running `/documents/{id}/parse` continues processing without re-upload.

## Known V0.1 limitations

- SQLite stores embeddings as JSON; production vector storage is not yet pgvector.
- default `hash_v1` vector is an architecture-validation embedding, not a production semantic model.
- BGE-M3 is optional and loaded in-process; production should use a persistent embedding worker.
- no asynchronous job queue yet; MinerU parsing request is synchronous.
- no user/RBAC/ACL enforcement yet; confidentiality metadata exists but is not authorization enforcement.
- metadata classification is filename-rule based; AI-assisted metadata review is planned.
- no folder scanner / bulk ingest yet.
- no webhook back to project management yet.
- no dedicated reranker yet; hybrid ranking combines lexical/vector scores.

## Live server smoke test

A real Uvicorn process was started on a temporary port and verified with HTTP requests:

- `GET /api/v1/health` → 200, `{status: ok, service: project-rag, version: 0.1.0}`
- `GET /` → 200

The test runtime database was removed before packaging.
