# Implementation plan

> This is the original plan, kept as a record. The system as built is described in the [README](../README.md) and the [ADRs](adr/).

Agreed scope for v0 of the auto claim intake voice agent. Vocabulary follows [GLOSSARY.md](../GLOSSARY.md); architectural decisions are recorded in [docs/adr](adr/).

## Product

- **Voice agent that takes the first notice of loss** for a fictional car insurer. The agent speaks Spanish (English later).
- **Claim fields**
  - Identification: `policy_number` + `plate`, validated against the seeded policies.
  - Declarant: `declarant_name`, `driver_is_policyholder` (bool); if false, `driver_name`.
  - Loss:
    - `loss_window = {start, end}`, resolved in `Europe/Madrid`, stored as ISO 8601 with offset. Collapses to an instant when the time is known; for `theft` / `parked_damage` it runs from "last seen" to "discovered".
    - `loss_location = {way?, municipality, province, country}`. Only Spain; `province` is validated against the 52 provinces. The LLM normalizes ("Valencia" → Valencia, Valencia, España) and asks when ambiguous; code validates.
    - `loss_type`: `collision | theft | broken_glass | parked_damage | other`.
    - `description` (free text), `injuries` (bool), `vehicle_immobilized` (bool).
  - Conditional fields:
    - `collision`: `other_vehicle_involved` (bool); if true, `other_vehicle_plate` and `friendly_report_signed` (bool).
    - `theft`: `police_report_filed` (bool).
  - **Urgency** (`high | medium | low`): derived by deterministic code from `injuries` and `vehicle_immobilized`, never asked.
- **Claim status**: `in_progress → pending_confirmation → confirmed`. A correction during confirmation keeps the claim `pending_confirmation` (and the bot reads back only the fields that changed) if it is still complete, or returns it to `in_progress` if the correction opens a new required field. Other final statuses: `incomplete`, `not_covered` and `escalated`, each storing a `closure_reason` (`data_refused | abandoned | retries_exhausted | system_error | outside_coverage_period | partial_coverage | outside_spain`) plus `pending_fields` (each with its last validation error). Partially collected data is kept.
- **Coverage**: loss window entirely within the coverage period → continue; entirely outside → `not_covered`; partly outside → `escalated`. Same for a location outside Spain (`not_covered`). Since the value may be misheard or a date wrongly resolved, the claim does not close right away: it stays `in_progress`, a template states the value (and the coverage period) and asks the declarant to confirm it; the claim closes only once they confirm it, and a correction re-evaluates it.
- **Closing**: the bot reads the claim back and the declarant confirms. The claim gets reference `SIN-<year>-<6-digit sequence>`, read aloud, and a Celery task generates the structured summary (key points, urgency, next steps).
- **Errors and edge cases**

  | Case | Behaviour |
  |---|---|
  | Empty or unintelligible transcription | Ask to repeat without calling the LLM, with a retry limit |
  | Invalid field (unknown policy, future date, plate mismatch) | Validator returns a specific error; the LLM explains it and asks again. 3 failures on the same field → `escalated` |
  | Loss outside the coverage period | State the date and the coverage period, ask to confirm; close as `not_covered` once confirmed, continue if corrected |
  | Injuries | Recommend calling 112, then continue |
  | Off-topic | Short answer, back to the interview |
  | Declarant refuses a required field | Explain why it's needed; if they insist, close as `incomplete` |
  | LLM fails (timeout, 5xx) or returns an invalid tool call | Retry with backoff; if it persists, apologise and keep the conversation stored |
  | TTS fails | Reply in text only |

- **Seeded policies** (5): happy path; driver ≠ policyholder; expired last month; started 3 days ago (a week-old loss falls outside); plate mismatch.

## Architecture

- **Code owns the flow** ([ADR-0002](adr/0002-code-owns-the-flow-llm-converses.md)). The LLM converses and proposes field values through an `update_claim` tool; Python validates them, holds the claim state and decides completeness, urgency, coverage and status transitions.
- **Hallucination guard**
  - Prevention: critical messages (confirmation read-back, reference, `not_covered`, the 112 advice) come from code templates, never from the LLM.
  - Detection: after each LLM reply, regex checks (mentions of `SIN-…`, "confirmada", "cubierto", € amounts, or a plate or policy number that differs from the validated ones) record a `hallucination_check` score in Langfuse and a WARNING log, without blocking the reply.
- **Modules**

  ```
  src/claims_agent/
    domain/     # pure, no I/O: Claim model, statuses, validators, urgency,
                # coverage, provinces, loss window resolution
    engine/     # turn loop: LLM + update_claim → validation → transitions;
                # prompts; templates; hallucination checks
    ports/      # async interfaces: LLM, SpeechToText, TextToSpeech, ConversationRepository,
                # Telemetry
    adapters/   # OpenRouter (chat, STT, TTS), Langfuse, in-memory repository
  src/claims_service/  # Django project: settings, ORM repository, ninja API, Channels
                       # consumer, Celery tasks (the ORM repository lives with its models)
  frontend/     # Vite + React + TypeScript
  tests/{unit,engine,service,e2e}
  evals/
  ```

  `domain/` imports nothing from the other packages; `engine/` depends only on `domain/` and `ports/`.
- **Stack**
  - Django with async views, django-ninja for HTTP and Channels for WebSocket ([ADR-0004](adr/0004-django-channels-websocket-for-turns.md)).
  - Postgres with `JSONField` ([ADR-0003](adr/0003-postgres-with-django-jsonfield.md)).
  - Redis (Channels layer and Celery broker) and Celery: `generate_claim_summary` after confirmation; `queue_missing_summaries` every 5 minutes re-queues it for confirmed claims that have none; `sweep_abandoned_conversations` with beat every minute (10 min of inactivity → `incomplete` / `abandoned`). The sweep selects the idle conversations and closes each with the domain's `close` (which records the pending fields) and the same versioned conditional `UPDATE` as a turn, so a turn that saved since the select wins; a turn marks the conversation active before calling the LLM, so a conversation receiving a turn is never swept.
  - Everything runs with `docker compose`.
- **WebSocket protocol**: the client sends `audio` (Ogg Opus encoded in the browser with WebCodecs, or WAV 16 kHz mono where it can't) or `text` (dev mode only in the UI; used by the evals). The server emits `transcript`, `bot_message`, `claim_updated` and `audio`.
- **Models** (all through OpenRouter, each behind a port):
  - LLM: `google/gemini-3.1-flash-lite` (chat completions with tools, no reasoning).
  - STT: `microsoft/mai-transcribe-2` (`POST /api/v1/audio/transcriptions`).
  - TTS: `microsoft/mai-voice-2.1-flash` with the `es-ES-Marta` voice (`POST /api/v1/audio/speech`).
- **Scalability**: stateless servers (conversation state lives only in the database, load → process → save per turn); async I/O end to end.
- **Observability**: Langfuse Cloud. Session = conversation, trace = turn, with spans for STT, LLM (as a generation, with tokens and cost through the Langfuse OpenAI wrapper pointed at OpenRouter), validation and TTS. Celery tasks trace into the same session. Structured JSON logs to stdout.
- **Frontend**: Vite + React + TypeScript. One page with a push-to-talk button, the transcript, bot audio and a live claim panel (each field empty / valid / invalid with its reason, urgency, status). The design will be chosen after comparing prototypes.
- **Tooling**: `uv`, `ruff`, `pytest`, `pydantic`, GitHub Actions (lint + tests). Code, README and ADRs in English; the agent speaks Spanish.

## Testing

1. Unit tests of `domain/`, no LLM.
2. Engine tests with a fake LLM that returns scripted tool calls.
3. **e2e** with real providers: audio fixtures → WebSocket → final claim. Marked `@pytest.mark.e2e`, excluded by default, run with `pytest -m e2e`.
4. Evals with an LLM-simulated declarant (5–6 scenarios) as a separate script.

## Implementation order

0. Spike: check Spanish quality of the STT and TTS models (10 minutes, before step 4).
1. `domain/` + unit tests.
2. `engine/` with a fake LLM + tests, then OpenRouter in text mode.
3. Django + Postgres + WebSocket in text mode + minimal React with the live panel.
4. Voice (STT and TTS).
5. Langfuse + hallucination regex checks.
6. Celery + Redis (summary, abandonment sweep).
7. e2e, evals, README, demo video.

If time runs short, cut from the bottom; never cut steps 1–4.

## Out of v0 (README: potential improvements)

- Real-time voice with streaming and barge-in.
- API replicas; rate limits and quotas per provider; model fallback.
- Multiple languages; other insurance lines (home).
- Real identity verification; resuming conversations; geocoding the loss location.
- Offline hallucination judge over Langfuse traces; alerting on failures and hallucination scores.
- Multi-tenancy.
- Bonus features (RAG, sentiment analysis).
