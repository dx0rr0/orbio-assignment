# Auto claim intake voice agent

A voice agent that takes the first notice of loss for a fictional car insurer (*Seguros Brisa*). The declarant talks to it in Spanish; it collects the claim, validates every field against the insured policy, reads the claim back for confirmation and issues a reference (`SIN-2026-000123`). A background task then writes a summary for the adjuster.

The vocabulary (loss, claim, declarant, loss window…) is defined in [GLOSSARY.md](GLOSSARY.md), the original plan in [docs/PLAN.md](docs/PLAN.md), the architectural decisions in [docs/adr](docs/adr), and known bugs and parked work in [docs/BUGS.md](docs/BUGS.md) and [docs/BACKLOG.md](docs/BACKLOG.md).

## Demo

Video walkthrough: _link to be added_ (happy path, a correction during the read-back, and a loss outside the coverage period).

## Running it

You need Docker and an [OpenRouter](https://openrouter.ai) API key. Langfuse is optional.

```bash
cp .env.example .env      # then set OPENROUTER_API_KEY, DJANGO_SECRET_KEY (and LANGFUSE_* for traces)
docker compose up --build
```

Open <http://localhost:8080>, allow the microphone and hold the button (or the space bar) to talk. The page looks like a call in progress: the whole conversation, with the agent's current words shown large, and on the right the claim as stages (identification, driver, what/when/where, damage, other vehicle or police report, confirmation) that fill in as you speak, with validation errors in Spanish. Add `?dev` to the URL to get a text box as well (handy without a microphone); `?conversation=<id>` reopens a conversation started in the same browser tab.

There are no accounts in v0. Each new conversation gets a random access token (only its hash is stored), which the page keeps in `sessionStorage`; resuming a conversation over WebSocket (a first `{"type": "resume", "token": ...}` message, not a URL query, so the token stays out of logs) or reading it through `GET /api/conversations/<id>` (`Authorization: Bearer <token>`) needs it, so a conversation can't be reopened from another browser. The list of all conversations, `GET /api/conversations`, is only served with `DJANGO_DEBUG=true`.

Five demo policies are seeded, with dates relative to today, one per scenario:

| Policy | Plate | Holder | Scenario |
|---|---|---|---|
| AUT-48213907 | 4721 KLM | María López Fernández | Happy path |
| AUT-51730284 | 8305 HJT | Javier Ruiz Martín | Someone else was driving |
| AUT-62947105 | 2196 FDS | Lucía Navarro Gil | Expired last month |
| AUT-73058416 | 6614 LPR | Carlos Moreno Díaz | Started three days ago (a week-old loss is not covered) |
| AUT-84169527 | 3087 GBN | Elena Sánchez Ortiz | Declare it with another plate to see the mismatch |

Services: `frontend` (nginx serving the React app and proxying `/api` and `/ws`), `backend` (Django on uvicorn), `worker` and `beat` (Celery), `db` (Postgres) and `redis`. Only the frontend is meant for the browser; the backend is reached through its proxy. The host ports are 8080, 55432 and 6380, chosen not to clash with local installs.

## How it works

```
browser ──Ogg Opus────▶ WebSocket consumer ──▶ STT ──▶ engine turn ──▶ TTS ──▶ browser
 (push-to-talk)          (Django Channels)              │
                                                        ├─ LLM: converses, proposes values via tools
                                                        ├─ domain: validates, decides status / urgency / coverage
                                                        └─ Postgres: conversation document (load → process → save)
                                        Celery: claim summary after confirmation (re-queued if lost) · abandonment sweep
```

**Code owns the flow; the LLM converses** ([ADR-0002](docs/adr/0002-code-owns-the-flow-llm-converses.md)). On each turn the LLM talks freely and proposes field values through an `update_claim` tool. Python validates them (policy exists, plate matches it, loss not in the future, province is one of the 52, …) and keeps only valid values. It also decides when the claim is complete, its urgency (from injuries and an immobilized vehicle, never asked), whether the policy covers the loss window, and every status transition. The LLM gets each rejection back as a specific error and explains it to the declarant. Three failures on the same field escalate the claim to an adjuster.

**Critical messages come from templates** ([ADR-0005](docs/adr/0005-critical-messages-from-templates.md)): the read-back, the reference, coverage decisions, the 112 advice and closings. Whenever the claim changes in a way only code may announce, the engine stops the LLM loop and speaks a template instead. The read-back is a short spoken summary of the loss (about 15 seconds): the policy number and plate were already checked against the policy, and the whole claim is on screen; the LLM echoes the date and place back as it records them. Before any text reaches the TTS, the LLM's replies too, code spells its identifiers out: letters one by one and digits in pairs ("A, U, T, 48, 21, 39, 07"), so the voice neither reads them as thousands nor guesses a word. A regex check flags LLM replies that mention references, confirmation, coverage, money or identifiers nobody gave; it records a Langfuse score and a warning, without blocking the reply.

**Packages.** `domain/` is pure (claim model, validators, lifecycle, coverage, provinces) and imports nothing else. `engine/` (turn loop, prompts, tools, templates, voice, summary) depends only on `domain/` and `ports/` (async interfaces for the LLM, speech, storage and telemetry). `adapters/` implements the ports for OpenRouter, Langfuse and in-memory storage. The Django project `claims_service/` stays at the edge: ORM repository, ninja API, Channels consumer and Celery tasks.

**Stateless servers.** A turn loads the conversation, processes it and saves it with an optimistic version check, so any process can serve any turn. A turn marks the conversation as active (a versioned save) before it calls the LLM, so the abandonment sweep, which closes with the same conditional update, skips a conversation that is receiving a turn; if the sweep closed it first, the turn tells the declarant the conversation is closed. Claim references come from an atomic Postgres upsert.

**Failure handling.** An empty or unintelligible transcription is answered without calling the LLM. LLM errors are retried with backoff; if they persist, the agent apologises and keeps the conversation, and three failed turns in a row escalate it. Three unintelligible utterances in a row escalate it too. Invalid tool calls go back to the LLM as errors. A failed transcription counts as unintelligible, and a failed synthesis ends the audio there: the text of every reply is always sent.

## Testing

```bash
uv sync
docker compose up -d db                       # the service tests use Postgres
uv run --env-file .env pytest                 # unit, engine and service tests
uv run pytest tests/unit tests/engine         # no .env or database needed
uv run mypy                                   # strict type checks (src, evals, scripts), no explicit Any
uv run lint-imports                           # the hexagonal layers: imports only point inwards
uv run --env-file .env pytest -m e2e          # needs the whole stack up and real providers
uv run --env-file .env python -m evals.run    # LLM-simulated declarants
uv run --env-file .env python scripts/chat.py # talk to the engine in text, real LLM, no database
cd frontend && npm ci && npm test && npm run lint
```

| Layer | What it checks |
|---|---|
| `tests/unit` | Domain rules, templates, hallucination regexes, next steps, JSON logs, the OpenRouter adapters against a mocked HTTP transport. No LLM. |
| `tests/engine` | The turn loop with a scripted fake LLM: tool handling, templates, retries, failures, escalations. Speech by sentence and the fixed-message speech cache, with fake STT and TTS; the claim summary. |
| `tests/service` | Postgres repository (versioning, concurrent reference numbers), WebSocket protocol, API, Celery tasks, server startup and shutdown. |
| `tests/e2e` | Recorded Spanish utterances (`tests/e2e/fixtures/audio`) through nginx, STT, LLM and TTS. The final claim is checked. |
| `evals/` | Seven scenarios where an LLM plays the declarant (happy path, another driver with injuries, a place corrected during the read-back, expired policy, loss before the policy started, insisting on a wrong plate, refusing to give a name). `evals/latency/` measures time to first audio ([docs/LATENCY.md](docs/LATENCY.md)). |
| `frontend` | WAV encoding and Ogg Opus muxing, PCM speech decoding and playback, the WebSocket protocol, access tokens, conversation state, claim stages and value formatting (vitest). |

GitHub Actions runs, for the backend (with a Postgres service), the suppression check, ruff, mypy, the import contracts and the non-e2e tests; for the frontend, lint, tests and the build.

Some bugs only showed up with real models, and each now has a regression test:
- The LLM filled every tool property with invented or empty values (`injuries: false`, `""`). Fields are now nullable and blank proposals are dropped before validation.
- A declarant insisting on a rejected plate was treated as a refusal instead of being escalated after three attempts.
- "Conducía yo" was recorded as a driver who is not the policyholder, because the LLM did not know who the policyholder was.

## Observability

Logs are one JSON object per line from Django, uvicorn and Celery. With the Langfuse keys set, each conversation is a Langfuse session and each turn (or background task) a trace. Traces contain spans for STT, validation and TTS, plus one generation per LLM call with its tokens and cost (through the Langfuse OpenAI wrapper pointed at OpenRouter). Each LLM reply gets a `hallucination_check` score.

## Models

All three go through OpenRouter, each behind a port:

- LLM: `google/gemini-3.1-flash-lite`, chat completions with tools, no reasoning (the fastest of the models measured for a turn, with the same extraction).
- STT: `microsoft/mai-transcribe-2`. In an early test of the models it transcribed all nine recorded utterances correctly, including policy numbers and plates. The one miss was a run of zeros ("AUT-00000001" lost a digit), so the seeded policy numbers avoid them.
- TTS: `microsoft/mai-voice-2.1-flash` with the `es-ES-Marta` voice (Spain accent), about 1.5 s faster to the whole reply than `google/gemini-3.8-flash-lite-tts`. It is asked for raw 24 kHz PCM, which goes to the browser as it arrives: each sentence of a reply is synthesized at the same time and played in order, so the first plays while the rest is still being synthesized. The speech of the fixed messages (the greeting first, synthesized when the server starts) is kept and replayed.

## Potential improvements

- Real-time voice: streaming the declarant's speech, a streamed LLM reply and barge-in. Turns are push-to-talk today, and only the reply's speech is streamed.
- Replicas behind a load balancer; rate limits and quotas per provider; model fallback.
- More languages; other insurance lines (home).
- Real identity verification and authentication (staff access to the conversation list, declarants resuming from another device); resuming a conversation after it closes; geocoding the loss location.
- An offline hallucination judge over Langfuse traces; alerts on failures and on hallucination scores.
- Multi-tenancy.

## Considered and left out

- **An adjuster view** (a back-office list of claims by urgency, with the summary and the transcript). Nothing in the brief needs it: conversations and claims are stored as JSON and readable through the API and in Langfuse. It would also need staff authentication over personal data. It is the natural next screen once there are adjusters.
- **Frustration detection.** A declarant stuck on a field already reaches a person: three failed attempts escalate the claim. Someone who has just had an accident sounds tense even when the call goes well, so false positives would make the agent condescending ("entiendo que esté frustrado"). Its real value is as a quality metric over many calls, which needs real traffic.
- **RAG.** The agent collects data and checks it against the policy, which it reads directly. The one plausible knowledge base, the policy conditions, would answer questions like "¿me cubre la grúa?", and a coverage answer is a contractual statement an LLM should not make: the adjuster gives it.
