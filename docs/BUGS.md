# Known bugs

Findings from a code review on 2026-10-21, ordered by severity. Each entry says where the bug lives, how to trigger it, what should happen instead and how to cover it with a test. Bugs B1, B2 and B5 were checked against the code; the rest come from the review (most reproduced with scripted fakes) and must be reproduced with a failing test before fixing.

Ground rules for whoever fixes them:

- Read [PLAN.md](PLAN.md), [GLOSSARY.md](../GLOSSARY.md) and [docs/adr](adr/) first. ADR-0002 (code owns the flow) and ADR-0005 (critical messages from templates) are the invariants most of these bugs break.
- Write the failing test first, then fix. Keep `domain/` free of imports from other packages and `engine/` dependent only on `domain/` and `ports/`.
- Run the checks with `uv run --env-file .env pytest -q`, `uv run ruff check .` and `uv run ruff format --check .`; for frontend changes, `npm run lint`, `npm test` and `npx tsc -b --noEmit` in `frontend/`.
- Tick the checkbox and add the commit hash when a bug is fixed.

## High

### B1. A claim can be confirmed without being read back

- [x] Fixed in d38751c
- **Where:** `src/claims_agent/engine/turns.py` (tool loop around lines 226–236; `_confirm_claim` around line 300).
- **Trigger:** the LLM returns `[update_claim(<fields completing the claim>), confirm_claim]` in one response (e.g. the declarant says "ya está todo, confírmelo"). Every tool call runs before `_needs_script` is checked, so the claim is `confirmed` with a reference and the read-back is never sent. Same with a correction plus `confirm_claim` while `pending_confirmation`: corrected data nobody heard gets confirmed. A declarant can provoke it on purpose (prompt injection).
- **Expected:** a claim is confirmed only in a turn after the read-back of exactly its current data was sent.
- **Fix:** stop running tool calls as soon as one leaves the claim needing a scripted message, and answer the remaining ones with an error result. Make `confirm_claim` require that the last read-back covered the claim's current data (store a hash or version of the claim that was read back on the conversation) and that it was sent in an earlier turn.
- **Tests:** engine tests with a fake LLM returning several tool calls in one response: complete + confirm, correction + confirm.

### B2. Policy data is used before the declarant is identified

- [x] Fixed in ca8689f
- **Where:** `src/claims_agent/domain/lifecycle.py` (`known_policy`, around lines 133–136; coverage check in `_next_status`, around lines 180–187); `src/claims_agent/engine/prompts.py` (policyholder name in the system prompt, around line 76); `src/claims_agent/engine/turns.py` (`_policy_of`); `src/claims_agent/engine/templates.py` (`not_covered` message with the coverage dates).
- **Trigger A:** "AUT-62947105, matrícula 1111 BBB, fue ayer". The plate is rejected, yet the policy still counts as known: coverage is evaluated and the `not_covered` template reads the policy's coverage dates aloud.
- **Trigger B:** with a wrong plate, the next LLM call already carries the policyholder's name in the system prompt, so "¿a nombre de quién está la póliza?" can leak a third party's personal data.
- **Expected:** a policy is "known" only when both `policy_number` and `plate` are validated against it. Until then, no policy data reaches the LLM and coverage is not evaluated.
- **Fix:** in the domain, derive the known policy from the claim having both identifiers accepted; in the engine, only fetch it (and pass it to the prompt) in that case.
- **Tests:** domain tests for coverage not being evaluated with a mismatched plate; engine test asserting the system prompt has no policyholder name until both identifiers are valid.

### B3. Failed attempts and refusals are counted per tool call, not per declarant turn

- [x] Fixed in c55ec32
- **Where:** `src/claims_agent/domain/lifecycle.py` (failed-attempt counting, around lines 155–158); `src/claims_agent/engine/turns.py` (`_declarant_refuses`, around line 320; `MAX_LLM_ROUNDS = 4`).
- **Trigger:** the LLM resends a rejected plate in 3 rounds of the same turn, and the claim becomes `escalated` / `retries_exhausted` after a single utterance. Two `declarant_refuses_field` calls in one response close the claim as `incomplete` without the explanation the plan requires.
- **Expected:** at most one failure and one refusal per field per declarant turn.
- **Fix:** track which fields already failed or were refused in the current turn, in the engine's per-turn state, and count each once.
- **Tests:** engine tests with a fake LLM retrying the same invalid value across rounds, and sending two refusals in one response.

### B4. An irreversible `not_covered` is decided on an unconfirmed value

- [x] Fixed in 2fc2357
- **Status (2026-10-21):** option (a), approved. Applies to the three coverage closures (outside Spain, outside the coverage period, partly outside); the LLM reports the confirmation with a new `declarant_confirms_value` tool.
- **Where:** `src/claims_agent/domain/lifecycle.py` (`_next_status`, around lines 180–187).
- **Trigger:** the LLM resolves "el martes pasado" to the wrong year, or the STT mishears the municipality as one abroad. The claim jumps straight to `not_covered`, a final status, and the declarant can't correct it.
- **Expected:** the declarant gets a chance to correct the value that makes the claim not covered before it closes.
- **Fix (needs a product decision, ask before implementing):** either (a) the bot states the problematic value from a template and asks the declarant to confirm it; the claim closes only after they confirm (a new intermediate status or a flag); or (b) coverage and location checks move to the read-back.
- **Tests:** domain or engine tests showing a correction after an out-of-coverage date reopens the flow.

### B5. Celery `.delay()` blocks the event loop

- [x] Fixed in e386fb0
- **Where:** `src/claims_service/intake/consumers.py`, `_request_summary` (around line 113).
- **Trigger:** with Redis slow or down, kombu's publish (with its connection retries and timeouts) runs synchronously inside the async consumer and freezes every WebSocket handled by that process. The current test only simulates an immediate exception, so it doesn't catch this.
- **Expected:** queueing the summary never blocks the event loop, and a failure to queue doesn't lose the summary.
- **Fix:** `await sync_to_async(generate_claim_summary.apply_async)(..., retry_policy=<short>)`. To not lose the summary when queueing fails, add a periodic task that queues confirmed claims without a summary (outbox-style).
- **Tests:** a consumer test where the publish is slow (sleeps) asserts other coroutines keep running; a task test for the outbox sweep.

### B6. The consumer doesn't handle unexpected exceptions

- [x] Fixed in fa6c124
- **Where:** `src/claims_service/intake/consumers.py`, `receive_json` (around lines 70–102; only `ConcurrentUpdateError` is caught) and `connect` (around line 49; nothing is caught).
- **Trigger:** a Postgres `OperationalError`, a `ConversationNotFoundError` or an assertion in the engine closes the socket with code 1011. No apology is sent, and the declarant's utterance is lost because the engine didn't save.
- **Expected (PLAN.md error table):** apologise with the `technical_problem` template and keep the conversation stored.
- **Fix:** a catch-all in `receive_json` and `connect` that logs with `logger.exception`, records the error in the trace and sends the `technical_problem()` message.
- **Tests:** consumer tests where the engine raises an unexpected exception.

## Medium

### B7. Race between the abandonment sweep and an in-flight turn

- [x] Fixed in acef69c
- **Where:** `src/claims_service/intake/tasks.py` (`sweep_abandoned_conversations`, around lines 65–82); `src/claims_agent/engine/turns.py` (save at the end of the turn, around lines 144–145).
- **Trigger:** the declarant speaks at 9:59 minutes of inactivity, the LLM takes 5 s and the sweep at 10:01 saves first. The turn's save raises `ConcurrentUpdateError`, the declarant hears "please repeat", and when they repeat the conversation is already closed. If the turn was a confirmation, a reference number is consumed (a gap in the sequence).
- **Expected:** a conversation that is receiving a turn is not swept.
- **Fix:** mark activity when the utterance arrives, before processing (touch `updated_at` / `last_activity`). On a conflict, reload the conversation and answer with the closed-conversation template instead of "please repeat". Fix the README sentence "a turn that lands mid-sweep wins", which is only true if the turn saves first.
- **Tests:** task test where the sweep saves first.

### B8. Unbounded worst-case turn latency

- [x] Fixed in a90aa49
- **Where:** `src/claims_agent/adapters/openrouter.py` (`TIMEOUT_SECONDS = 30`); `src/claims_agent/engine/turns.py` (`MAX_LLM_ROUNDS`, `LLM_RETRY_DELAYS`).
- **Trigger:** 4 LLM rounds × 3 attempts × 30 s timeout ≈ 6 minutes for one turn. The consumer processes messages serially, so nothing else is handled meanwhile.
- **Expected:** a per-turn deadline (e.g. 20–25 s) after which the bot apologises with the `technical_problem` template.
- **Fix:** wrap the turn in `asyncio.timeout(...)` and lower the per-request timeout.
- **Tests:** engine test with a fake LLM that never answers.

### B9. Lifecycle of the HTTP clients

- [x] Fixed in 4c72fe6
- **Status (2026-10-21):** the speech adapters keep one pooled client each for the life of the process and close it on ASGI lifespan shutdown (`ConsumerServices.aclose`). The consumer's chat client is closed the same way. The summary task opens a chat client per task on purpose (each task runs in its own event loop, and a client is bound to the loop it first ran on) and closes it when the task is done with it (`open_chat_model`).
- **Where:** `src/claims_agent/adapters/openrouter.py` (around line 94, `httpx.AsyncClient` never closed); `src/claims_service/intake/tasks.py` (around line 43, a new client per task).
- **Expected:** one client per process, closed on shutdown.
- **Fix:** create the clients once (module-level factory or app/worker startup) and close them on ASGI lifespan shutdown and Celery `worker_shutdown`.

### B13. A correction leaves a stale `description`

- [x] Fixed in f0e38c3
- **Status (2026-10-21):** the first option: the `description` field now says to keep out the place and the time. The `location_corrected_in_read_back` scenario passed 3/3 with the fix, but also 3/3 without it: with the current chat model (Gemini 3.1 Flash Lite) the description never carried the place, so the bug didn't reproduce. The fix stays as a guard against a model change.
- **Found:** 2026-10-21, rehearsing the demo video in text with `scripts/chat.py` and the real LLM.
- **Where:** `src/claims_agent/engine/tools.py` (`description` property, around line 56: "What happened, in the declarant's words, summarized"); `src/claims_agent/engine/prompts.py` (how corrections during the read-back are handled).
- **Trigger:** the declarant says "ayer sobre las seis, en un semáforo de la calle Madrid, en Getafe, un coche me dio por detrás…". The LLM copies the place into `description` ("En un semáforo de la calle Madrid, en Getafe, otro coche le golpeó por detrás"). During the read-back the declarant corrects "no fue en Getafe, fue en Leganés": `loss_location` becomes Leganés, `description` is not re-proposed, and the second read-back (and the confirmed claim) says both Leganés and Getafe.
- **Expected:** a confirmed claim has no contradictions between its fields; a correction updates every field that repeats the corrected fact.
- **Fix:** either keep `description` to what happened, without the place or time (they have their own fields), or tell the LLM to re-propose `description` whenever a correction touches something it mentions. The first is simpler and also shortens the read-back.
- **Tests:** engine test with a scripted fake LLM is not enough here (the bug is the LLM's choice); cover the prompt/tool wording with an eval scenario: a correction of the location during the read-back, asserting the final `description` doesn't mention the old place.

## Low

### B10. An empty `end` in `loss_window` is rejected

- [x] Fixed in 40f05fd
- **Where:** `src/claims_agent/domain/loss_window.py` (`parse_loss_window`, `raw.get("end") is not None`); `src/claims_agent/engine/tools.py` (`stated_values`, around lines 81–86, doesn't strip nested blanks).
- **Trigger:** the tool schema marks `end` as required, so the model sends `{"start": "...T19:00", "end": ""}`. That raises a `FieldError` and counts a failed attempt.
- **Fix:** treat a blank `end` as absent.
- **Tests:** a domain unit test.

### B11. One-character answers count as unintelligible

- [x] Fixed in ef391a9
- **Where:** `src/claims_agent/engine/turns.py` (the `\w{2,}` check, around lines 61 and 89–91).
- **Trigger:** "¿en qué kilómetro?" → "8". The bot asks to repeat, and the streak counts towards escalation.
- **Fix:** accept any utterance with at least one word character.
- **Tests:** an engine test.

### B12. Consumer messages in English reach the declarant

- [x] Fixed in 7e3263c
- **Where:** `src/claims_service/intake/consumers.py` (around lines 94, 140 and 145, e.g. "the conversation changed meanwhile…").
- **Fix:** route them through the Spanish templates or `error_messages.py`.

## Security

### S1. No authentication on conversations

- [x] Fixed in 86e2cbb
- **Status (2026-10-21):** done as proposed and approved: listing only in DEBUG; per-conversation token (hash stored) required on the WebSocket (first sent as `?token=`, now in a first `resume` message so it stays out of URLs and logs) and the API (`Authorization: Bearer`); kept in `sessionStorage`, so resuming from another browser is not possible.
- **Where:** `src/claims_service/intake/api.py` (around lines 24–35); `src/claims_service/intake/consumers.py` (around lines 57–63).
- **Problem:** the API lists every conversation with personal data (names, plates, transcripts). With those ids anyone can connect to `/ws/conversations/<id>/` and inject turns into someone else's conversation.
- **Fix for v0:** remove or protect the listing endpoint (staff-only via Django auth, or a setting that disables it outside debug). Issue an unguessable per-conversation token when the conversation is created, and require it on the WebSocket. Document real authentication as an improvement.

### S2. Deployment hardening

- [x] Fixed in 2b71e69
- **Where:** `src/claims_service/asgi.py` (around lines 14–24, no `AllowedHostsOriginValidator`); `docker-compose.yml` (around line 8, `DJANGO_ALLOWED_HOSTS: "*"`; backend port 8000 exposed); `.env.example` (`DJANGO_DEBUG=true`, which compose uses).
- **Fix:** wrap the WebSocket router in `AllowedHostsOriginValidator`, set explicit allowed hosts, default `DEBUG` to false, and expose only the frontend.

### S3. Cost amplification and input sizes

- [ ] Fixed
- **Status (2026-10-21):** text length capped in 847f4ad. Greeting audio cache, `--ws-max-size`, WAV header check and rejecting text outside dev mode are left to the voice pipeline redesign; a per-IP rate limit is still open.
- **Where:** `src/claims_service/intake/consumers.py` (around lines 48–55: each connection creates a row and synthesizes the greeting; around lines 75–78: no text length limit).
- **Problems:** no rate limit; text messages have no length limit and are accepted even outside dev mode; the 3 M-character audio limit is checked after uvicorn has already accepted frames of up to 16 MB (its default `ws_max_size`); the payload is not checked to be a WAV.
- **Fix:** cache the greeting audio (it is constant); cap the text length and reject text messages unless dev mode is on; set uvicorn's `--ws-max-size` to the audio limit; check the WAV header; add a simple per-IP connection rate limit.

## Tests and tooling

### T1. Unit tests need Django settings

- [x] Fixed in d994a85
- **Problem:** `uv run pytest` without `--env-file .env` fails at startup with `RuntimeError: environment variable DJANGO_SECRET_KEY is required`, because pytest-django loads the settings globally, even for the pure domain tests.
- **Fix:** give the test run a default secret key (pytest-django's `DJANGO_SETTINGS_MODULE` pointing to test settings, or an env default in `pyproject.toml`), so `uv run pytest` works out of the box.

### T2. Fragile e2e wait

- [ ] Fixed
- **Status (2026-10-21):** deferred: the audio events of a turn change with the voice pipeline redesign.
- **Where:** `tests/e2e/test_voice_scenarios.py` (`turn()`, around line 34).
- **Problem:** it always waits for an `audio` event, so a TTS failure becomes a 90 s timeout instead of a clear failure.
- **Fix:** wait for the end-of-turn event and assert on the audio separately.

### T3. Missing type checking in CI

- [x] Fixed in 7dbe8b3
- **Fix:** add `mypy` or `pyright` to the dev group and to `.github/workflows/ci.yml`; the code is fully annotated but nothing checks it.

## Plan and documentation deviations

- [x] **D1.** (fixed in 9e5c646) `README.md` has no demo video URL, which is a deliverable in the assignment PDF. Add a placeholder section now.
- [x] **D2.** (fixed in 286dea2) The abandonment sweep is a SELECT plus one conditional UPDATE per row (by version), not the single atomic UPDATE the plan describes. Either change the code or update `PLAN.md`.
- [x] **D3.** (fixed in 286dea2) `scripts/chat.py` is not mentioned in the README.
- [x] **D4.** (fixed in 3421533) A failed summary task leaves no trace after 3 retries (no status, no explicit error log), and the LLM-written key points don't go through the hallucination checks.
