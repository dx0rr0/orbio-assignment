# Postgres with Django JSONField for storage

Supersedes ADR-0001. Once Celery and Redis came in, the setup needed `docker compose` anyway, so "no extra services" no longer favoured SQLite. Its single writer would also clash with Django and Celery workers writing at the same time. Claims stay document-shaped (a `JSONField` validated by the domain's Pydantic models) inside Postgres, managed through the Django ORM.
