# Backlog

Ideas agreed as worth doing, parked for later. Not bugs (those are in [BUGS.md](BUGS.md)) and not yet in [PLAN.md](PLAN.md).

## Alerts on failures, escalations and hallucinations

A Celery beat task every 5 minutes looks at the last 15 minutes in Postgres and computes three rates: system failures (conversations closed with `system_error`, or `technical_problem` messages sent), escalations (`escalated` claims) and hallucination flags (turns where the regex check fired). Each is compared with a threshold from settings. The hallucination result must also be stored on the conversation, not only as a Langfuse score, so alerting doesn't depend on an external API. On a breach: a structured `ERROR` log and, if `ALERT_WEBHOOK_URL` is set, a Slack-compatible webhook POST, with a Redis key giving a 30-minute cooldown per alert. Tests seed data above and below each threshold. For the demo video, an invalid API key triggers it. Prometheus + Alertmanager or Grafana go to the README as the production path. Touches `intake/tasks.py`, `celery.py`, `settings.py` and the conversation model. Estimate: 3–4 h.


## Known design debt

Found in a review and left for after the submission on purpose, because each one touches the conversation flow.

**Conditional-field rules are written twice, inverted.** `Claim.required_fields` (`src/claims_agent/domain/claim.py`) says when a conditional field is required; `_inapplicable_fields` (`src/claims_agent/domain/lifecycle.py`) says when it is known not to apply, so its value is dropped. They encode the same conditions (driver not the policyholder, collision, another vehicle involved, theft) and can drift apart. Both should derive from one declarative table of field, condition and position. B14 in [BUGS.md](BUGS.md) (a hit-and-run requiring the police report instead of the plate) would today have to change both.

**Some business rules live in the engine rather than the domain.** Counting refusals and closing the claim as `incomplete` after `MAX_REFUSALS` (`_declarant_refuses`), and escalating after `MAX_REPEAT_REQUESTS` unintelligible utterances (`_unintelligible`) or `MAX_LLM_FAILURES` turns without a usable LLM reply (`_converse`), are all in `src/claims_agent/engine/turns.py`, with their counters on the conversation. `MAX_FAILED_ATTEMPTS` and the escalation it triggers are in `src/claims_agent/domain/lifecycle.py`. The thresholds and the closures they lead to are claim rules and belong next to it, where they could be unit-tested without the turn loop.

**`coverage_issue` is typed as `ClosureReason`.** `Claim.coverage_issue` (`src/claims_agent/domain/claim.py`) only ever holds one of the three keys of `COVERAGE_ISSUES`, but the type admits every closure reason (`data_refused`, `abandoned`…), and `value_to_confirm` would fail on one of those. A dedicated `CoverageIssue` enum, mapped to its closure reason when the claim closes, would express the concept and let the type checker enforce it.
