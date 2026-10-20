---
status: superseded by ADR-0003
---

# SQLite with JSON documents for storage

Conversations, claims and policies are stored in SQLite, as JSON documents in columns, behind a repository. We rejected loose JSON files (no atomic writes, no safe concurrency) and MongoDB/Postgres in Docker (they add a service to the evaluator's setup and bring nothing at this scale). Because the storage is document-shaped, moving to Postgres JSONB or MongoDB means swapping the repository adapter.
