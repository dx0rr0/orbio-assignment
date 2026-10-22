# Backlog

Ideas agreed as worth doing, parked for later. Not bugs (those are in [BUGS.md](BUGS.md)) and not yet in [PLAN.md](PLAN.md).

## Alerts on failures, escalations and hallucinations

A Celery beat task every 5 minutes looks at the last 15 minutes in Postgres and computes three rates: system failures (conversations closed with `system_error`, or `technical_problem` messages sent), escalations (`escalated` claims) and hallucination flags (turns where the regex check fired). Each is compared with a threshold from settings. The hallucination result must also be stored on the conversation, not only as a Langfuse score, so alerting doesn't depend on an external API. On a breach: a structured `ERROR` log and, if `ALERT_WEBHOOK_URL` is set, a Slack-compatible webhook POST, with a Redis key giving a 30-minute cooldown per alert. Tests seed data above and below each threshold. For the demo video, an invalid API key triggers it. Prometheus + Alertmanager or Grafana go to the README as the production path. Touches `intake/tasks.py`, `celery.py`, `settings.py` and the conversation model. Estimate: 3–4 h.

