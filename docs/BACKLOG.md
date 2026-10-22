# Backlog

Ideas agreed as worth doing, parked for later. Not bugs (those are in [BUGS.md](BUGS.md)) and not yet in [PLAN.md](PLAN.md).

## Adjuster view in the Django admin

A back-office list of claims ordered by urgency and status, with the summary, the transcript and a link to the conversation's Langfuse session. Optional: a live feed of high-urgency claims through a Channels group. Touches `intake/models.py`, a new `intake/admin.py`, `groups.py` and `presenters.py`. Estimate: 2 h for the admin, 4–5 h with the live feed.

## Alerts on failures, escalations and hallucinations

A Celery beat task every 5 minutes looks at the last 15 minutes in Postgres and computes three rates: system failures (conversations closed with `system_error`, or `technical_problem` messages sent), escalations (`escalated` claims) and hallucination flags (turns where the regex check fired). Each is compared with a threshold from settings. The hallucination result must also be stored on the conversation, not only as a Langfuse score, so alerting doesn't depend on an external API. On a breach: a structured `ERROR` log and, if `ALERT_WEBHOOK_URL` is set, a Slack-compatible webhook POST, with a Redis key giving a 30-minute cooldown per alert. Tests seed data above and below each threshold. For the demo video, an invalid API key triggers it. Prometheus + Alertmanager or Grafana go to the README as the production path. Touches `intake/tasks.py`, `celery.py`, `settings.py` and the conversation model. Estimate: 3–4 h.

## Frustration detection where code decides

Each turn, the LLM (or a small classifier) reports a frustration score from 0 to 1 alongside its tool calls; the code decides what to do with it (ADR-0002). The bot says an empathy line from a template and, after N frustrated turns, offers to hand over to a person. The score is recorded in Langfuse, and a new eval scenario covers it. Reuses the assignment's sentiment-analysis bonus. Touches `engine/tools.py`, `engine/turns.py`, `engine/templates.py` and `evals/scenarios.py`. Estimate: 2–3 h; the main risk is false positives.
