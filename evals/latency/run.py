"""Time-to-first-audio eval: each case is one declarant turn through the real WebSocket
consumer, with the real STT, LLM and TTS on OpenRouter.

    uv run --env-file .env --env-file .env.eval python -m evals.latency.run --variant baseline
    uv run ... python -m evals.latency.run --variant v1 --split train
    uv run ... python -m evals.latency.run --variant v1 --cases R05 R10 --reps 2

The consumer runs with the production services, except for the repository and the access
tokens: the conversation each case starts from is put in memory, so nothing touches the
development database. Langfuse is off; everything is recorded under
`evals/latency/results/<variant>/`: `results.jsonl` (one row per case and rep), `traces/`,
`errors.jsonl` and `summary.json`. One unrecorded turn (WARM_UP) runs first, so the cold start
(TLS handshakes, the first LLM call) doesn't land on the first case.

Spending is checked against the OpenRouter key before every case: the run stops once the key
has used BUDGET_STOP dollars. The key's own limit is the hard cap. An infrastructure error (a
failed STT or TTS, the technical-problem apology after LLM failures) stops the run too: it
would fail every case the same way.
"""

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import statistics
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

import httpx
from pydantic import JsonValue

# Langfuse off and dev defaults, before Django reads the settings.
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.setdefault("DJANGO_DEBUG", "true")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "claims_service.settings")

from channels.security.websocket import OriginValidator
from channels.testing import WebsocketCommunicator

from claims_agent.adapters.memory import InMemoryConversationRepository
from claims_agent.domain import (
    MADRID,
    Claim,
    Conversation,
    Message,
    Role,
    apply_update,
    policy_number_to_look_up,
)
from claims_agent.engine import Voice, templates
from claims_agent.ports import ChatModel, Completion, NullTelemetry, Span, ToolSpec
from claims_agent.seed import seed_policies
from claims_service.intake.presenters import ServerEvent
from evals.latency.cases import CASES, TEST_IDS, Case, read_back_text, resolve

logger = logging.getLogger(__name__)

RESULTS = Path(__file__).parent / "results"
AUDIO = Path(__file__).parent / "audio"
HARNESS = [Path(__file__), Path(__file__).parent / "cases.py"]
BUDGET_STOP = 0.78
CASE_TIMEOUT_S = 90
# A cold first connection (imports, joining the Redis group) can take longer than the
# communicator's default of 1 s.
CONNECT_TIMEOUT_S = 10
# The shortest clip ("Sí."), run once before the cases and not recorded.
WARM_UP = "R05"
# What the engine says after the LLM failed; a case answered with it measures nothing.
TECHNICAL_PROBLEM = templates.technical_problem().text


# --- instrumentation ----------------------------------------------------------------------------

TokenCount = Literal["input_tokens", "cached_input_tokens", "output_tokens"]
TOKEN_COUNTS: tuple[TokenCount, ...] = ("input_tokens", "cached_input_tokens", "output_tokens")


class LLMCall(TypedDict):
    latency_s: float
    # A failed call has the error; a completed one the token counts.
    error: NotRequired[str]
    input_tokens: NotRequired[int]
    cached_input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]


@dataclass
class Meter:
    llm_calls: list[LLMCall] = field(default_factory=list)
    # Why the voice failed to transcribe ("stt") or to speak ("tts"), as it recorded it.
    speech_errors: dict[str, str] = field(default_factory=dict)
    speech_failed: asyncio.Event = field(default_factory=asyncio.Event)

    def reset(self) -> None:
        self.llm_calls.clear()
        self.speech_errors.clear()
        self.speech_failed.clear()


class _SpeechSpan:
    def __init__(self, name: str, meter: Meter) -> None:
        self._name = name
        self._meter = meter

    def update(self, *, output: JsonValue = None, metadata: JsonValue = None) -> None:
        if isinstance(metadata, dict) and "error" in metadata:
            self._meter.speech_errors[self._name] = str(metadata["error"])
            self._meter.speech_failed.set()


class SpeechTelemetry(NullTelemetry):
    """No traces (Langfuse is off), but the reason the voice gives when STT or TTS fail: a
    failed synthesis sends no audio, so the case would otherwise wait for it until timeout."""

    def __init__(self, meter: Meter) -> None:
        self._meter = meter

    @contextmanager
    def span(self, name: str, *, input: JsonValue = None) -> Iterator[Span]:
        if name in ("stt", "tts"):
            yield _SpeechSpan(name, self._meter)
        else:
            with super().span(name, input=input) as span:
                yield span


class MeteredChatModel:
    """The production chat model, timing each completion, failed ones too."""

    def __init__(self, llm: ChatModel, meter: Meter) -> None:
        self._llm = llm
        self._meter = meter

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> Completion:
        started = time.perf_counter()
        try:
            completion = await self._llm.complete(system=system, messages=messages, tools=tools)
        except Exception as error:
            latency = round(time.perf_counter() - started, 3)
            self._meter.llm_calls.append({"latency_s": latency, "error": repr(error)})
            raise
        usage = completion.usage
        self._meter.llm_calls.append(
            {
                "latency_s": round(time.perf_counter() - started, 3),
                "input_tokens": usage.input_tokens if usage else 0,
                "cached_input_tokens": usage.cached_input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
            }
        )
        return completion


class InMemoryAccess:
    """Access tokens for the conversations in memory, checked as `intake.access` does."""

    def __init__(self) -> None:
        self._tokens: dict[UUID, str] = {}

    async def issue_token(self, conversation_id: UUID) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[conversation_id] = token
        return token

    async def is_authorized(self, conversation_id: UUID, token: str | None) -> bool:
        stored = self._tokens.get(conversation_id)
        return stored is not None and token is not None and hmac.compare_digest(stored, token)


def key_usage() -> float:
    response = httpx.get(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        timeout=30,
    )
    response.raise_for_status()
    return float(response.json()["data"]["usage"])


# --- harness gate -------------------------------------------------------------------------------


def harness_sha() -> str:
    digest = hashlib.sha256()
    for path in HARNESS:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_state() -> dict[str, JsonValue]:
    path = RESULTS / "_state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(state: dict[str, JsonValue]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


# --- one case -----------------------------------------------------------------------------------


def initial_conversation(case: Case, now: datetime) -> Conversation:
    policies = {p.number: p for p in seed_policies(now.date())}
    claim = Claim()
    if case.known:
        known = resolve(case.known, now)
        number = policy_number_to_look_up(claim, known)
        result = apply_update(claim, known, policy=policies.get(number or ""), now=now)
        if result.rejected:
            raise ValueError(f"{case.id}: known fields rejected: {result.rejected}")
        claim = result.claim
    messages = [
        Message(role=Role(role), content=text, scripted=role == "assistant")
        for role, text in case.history
    ]
    asked = case.asked if case.asked is not None else read_back_text(claim)
    messages.append(Message(role=Role.ASSISTANT, content=asked, scripted=True))
    return Conversation(claim=claim, messages=tuple(messages), created_at=now, updated_at=now)


def trace_of(conversation: Conversation) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for message in conversation.messages:
        if message.role is Role.TOOL:
            turns.append({"role": "tool_result", "content": message.content or ""})
            continue
        if message.content:
            turns.append({"role": message.role.value, "content": message.content})
        turns.extend(
            {"role": "tool_call", "name": call.name, "content": call.arguments}
            for call in message.tool_calls
        )
    return turns


@dataclass(frozen=True)
class Harness:
    """The consumer's application and what the cases share with it."""

    application: OriginValidator
    repository: InMemoryConversationRepository
    access: InMemoryAccess
    meter: Meter
    # The provider clients, closed at the end of the run.
    resources: AsyncExitStack


async def next_event(communicator: WebsocketCommunicator, meter: Meter) -> ServerEvent:
    """The next event from the consumer, unless the voice reports a failure first."""
    receive = asyncio.ensure_future(communicator.receive_json_from(timeout=CASE_TIMEOUT_S))
    speech_failed = asyncio.ensure_future(meter.speech_failed.wait())
    done, _ = await asyncio.wait({receive, speech_failed}, return_when=asyncio.FIRST_COMPLETED)
    speech_failed.cancel()
    if receive in done:
        event: ServerEvent = receive.result()
        return event
    receive.cancel()
    raise RuntimeError(f"speech failed: {meter.speech_errors}")


Grade = TypedDict("Grade", {"pass": float, "checks": float})


class CaseRow(TypedDict):
    """A row of `results.jsonl`: `run_case` returns it with the trace, which goes to its own
    file, and without the rep, which the run adds."""

    prompt_id: str
    prompt: str
    tags: list[str]
    grade: Grade
    checks: dict[str, bool]
    ttfa_s: float
    stt_s: float
    engine_s: float
    tts_first_s: float
    llm_calls: int
    llm_failed: int
    llm_s: float
    usage: dict[str, int]
    replies: list[str]
    status: str
    rep: NotRequired[int]
    _trace: NotRequired[list[dict[str, str]]]


async def run_case(case: Case, harness: Harness) -> CaseRow:
    now = datetime.now(MADRID)
    conversation = await harness.repository.create(initial_conversation(case, now))
    token = await harness.access.issue_token(conversation.id)
    wav = (AUDIO / case.audio).read_bytes()
    meter = harness.meter
    meter.reset()

    communicator = WebsocketCommunicator(
        harness.application,
        f"/ws/conversations/{conversation.id}/",
        headers=[(b"origin", b"http://localhost")],
    )
    marks: dict[str, float] = {}
    replies: list[str] = []
    transcript = ""
    try:
        connected, _ = await communicator.connect(timeout=CONNECT_TIMEOUT_S)
        if not connected:
            raise RuntimeError("websocket refused")
        await communicator.send_json_to({"type": "resume", "token": token})
        snapshot = await communicator.receive_json_from(timeout=10)
        if snapshot["type"] != "snapshot":
            raise RuntimeError(f"resume refused: {snapshot}")

        started = time.perf_counter()
        await communicator.send_json_to({"type": "audio", "data": base64.b64encode(wav).decode()})
        # The turn is measured once the claim is saved and the first audio is out. A receive
        # timeout cancels the consumer (asgiref), so the loop never waits for silence.
        while not {"claim_updated", "audio"} <= marks.keys():
            event = await next_event(communicator, meter)
            marks.setdefault(event["type"], round(time.perf_counter() - started, 3))
            if event["type"] == "transcript":
                transcript = event["text"]
                if not transcript:
                    reason = meter.speech_errors.get("stt", "no error recorded")
                    raise RuntimeError(f"empty transcript (the STT failed: {reason})")
            elif event["type"] == "bot_message":
                if event["text"] == TECHNICAL_PROBLEM:
                    failed = [c["error"] for c in meter.llm_calls if "error" in c]
                    raise RuntimeError(f"the engine apologised for a technical problem: {failed}")
                replies.append(event["text"])
            elif event["type"] == "error":
                raise RuntimeError(f"server error: {event['message']}")
    finally:
        # A consumer still sending (or already cancelled by a timeout) makes disconnect raise.
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await communicator.disconnect()

    after = await harness.repository.get(conversation.id)
    checks = {name: bool(check(after.claim, replies, now)) for name, check in case.checks.items()}
    stt = marks.get("transcript", 0.0)
    engine_done = marks.get("bot_message", marks.get("claim_updated", stt))
    return {
        "prompt_id": case.id,
        "prompt": transcript,
        "tags": list(case.tags),
        "grade": {
            "pass": float(all(checks.values())),
            "checks": sum(checks.values()) / len(checks),
        },
        "checks": checks,
        "ttfa_s": marks["audio"],
        "stt_s": stt,
        "engine_s": round(engine_done - stt, 3),
        "tts_first_s": round(marks["audio"] - engine_done, 3),
        "llm_calls": len(meter.llm_calls),
        "llm_failed": sum("error" in c for c in meter.llm_calls),
        "llm_s": round(sum(c["latency_s"] for c in meter.llm_calls), 3),
        "usage": {name: sum(c.get(name, 0) for c in meter.llm_calls) for name in TOKEN_COUNTS},
        "replies": replies,
        "status": "ok",
        "_trace": trace_of(after),
    }


# --- the run ------------------------------------------------------------------------------------


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def build_harness() -> Harness:
    """The consumer with the production services but for the repository and the access
    tokens, which live in memory with the conversations, and the meters around them."""
    from claims_agent.engine import ConversationEngine
    from claims_service.intake import services
    from claims_service.intake.routing import websocket_application

    repository = InMemoryConversationRepository(seed_policies(datetime.now(MADRID).date()))
    access = InMemoryAccess()
    meter = Meter()
    resources = AsyncExitStack()
    # What production runs with when Langfuse is off, plus the speech failures.
    telemetry = SpeechTelemetry(meter)
    chat_model = services.chat_model_from_settings()
    resources.push_async_callback(chat_model.aclose)
    llm = MeteredChatModel(chat_model, meter)
    consumer_services = services.ConsumerServices(
        engine=ConversationEngine(
            llm=llm,
            conversations=repository,
            policies=repository,
            references=repository,
            telemetry=telemetry,
        ),
        voice=Voice(*services.speech_from_settings(resources), telemetry),
        repository=repository,
        telemetry=telemetry,
        access=access,
        request_summary=services.request_summary,
    )
    application = websocket_application(consumer_services)
    return Harness(application, repository, access, meter, resources)


async def run(variant: str, case_ids: list[str] | None, reps: int, split: str = "all") -> int:
    import django

    django.setup()
    harness = build_harness()
    async with harness.resources:
        return await run_cases(harness, variant, case_ids, reps, split)


async def run_cases(
    harness: Harness, variant: str, case_ids: list[str] | None, reps: int, split: str
) -> int:
    cases = [
        c
        for c in CASES
        if (case_ids is None or c.id in case_ids)
        and (split == "all" or (c.id in TEST_IDS) == (split == "test"))
    ]
    out = RESULTS / variant
    (out / "traces").mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done.add((row["prompt_id"], row["rep"]))

    def record_error(case_id: str, rep: int | str, error: Exception) -> None:
        with (out / "errors.jsonl").open("a", encoding="utf-8") as errors:
            entry = {
                "prompt_id": case_id,
                "rep": rep,
                "error": repr(error),
                "llm_calls": list(harness.meter.llm_calls),
                "speech_errors": dict(harness.meter.speech_errors),
            }
            errors.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # Infrastructure fails the same way for every case: stop before paying for more.
        print(f"  {case_id} rep{rep}: ERROR {error!r}; stopping the run")

    pending = [(rep, case) for rep in range(reps) for case in cases if (case.id, rep) not in done]
    start_usage = key_usage()
    print(f"{variant}: {len(cases)} cases x {reps} reps, key usage ${start_usage:.4f}")
    if pending:
        if start_usage >= BUDGET_STOP:
            print(f"budget stop: key usage ${start_usage:.4f} >= ${BUDGET_STOP}")
            return 3
        try:
            await run_case(next(c for c in CASES if c.id == WARM_UP), harness)
        except Exception as error:  # recorded, never scored
            logger.exception("The warm-up turn failed")
            record_error(WARM_UP, "warm-up", error)
            return 4
    for rep, case in pending:
        before = key_usage()
        if before >= BUDGET_STOP:
            print(f"budget stop: key usage ${before:.4f} >= ${BUDGET_STOP}")
            return 3
        try:
            row = await run_case(case, harness)
        except Exception as error:  # recorded, never scored
            logger.exception("Case %s failed", case.id)
            record_error(case.id, rep, error)
            return 4
        row["rep"] = rep
        trace = row.pop("_trace")
        (out / "traces" / f"{case.id}_rep{rep}.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with results_path.open("a", encoding="utf-8") as results:
            results.write(json.dumps(row, ensure_ascii=False) + "\n")
        failed = [name for name, ok in row["checks"].items() if not ok]
        print(
            f"  {case.id} rep{rep}: ttfa {row['ttfa_s']:.2f}s "
            f"(stt {row['stt_s']:.2f} · engine {row['engine_s']:.2f} · "
            f"tts {row['tts_first_s']:.2f}) llm x{row['llm_calls']} "
            f"{'PASS' if not failed else 'FAIL ' + ','.join(failed)}"
        )

    # The key's usage lags behind the calls by a few seconds, so cost is only measured per run.
    await asyncio.sleep(10)
    spent = key_usage() - start_usage
    # The warm-up turn is paid for like a case.
    turns = len(pending) + 1 if pending else 0
    summary: dict[str, JsonValue] = {
        "spent_this_run_usd": round(spent, 6),
        "cost_per_case_usd": round(spent / turns, 6) if turns else None,
    }
    rows = (
        [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
        if results_path.exists()
        else []
    )
    if rows:
        ttfa = [r["ttfa_s"] for r in rows]
        summary |= {
            "cases": len({r["prompt_id"] for r in rows}),
            "rows": len(rows),
            "ttfa_p50_s": round(statistics.median(ttfa), 2),
            "ttfa_p95_s": round(percentile(ttfa, 0.95), 2),
            "pass_rate": round(statistics.mean(r["grade"]["pass"] for r in rows), 3),
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", required=True, help="baseline or v<N>")
    parser.add_argument("--cases", nargs="*", help="case ids (default: all)")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument(
        "--split",
        choices=["all", "train", "test"],
        default="all",
        help="rounds run on train; test only validates the result",
    )
    parser.add_argument(
        "--approve-harness",
        action="store_true",
        help="record the current runner and cases as approved (the user's call)",
    )
    args = parser.parse_args()

    state = load_state()
    if args.approve_harness:
        state["harness_sha"] = harness_sha()
        save_state(state)
        print("harness approved")
        return 0
    if state.get("harness_sha") != harness_sha():
        print(
            "the runner or the cases changed since they were approved; review them and run "
            "again with --approve-harness"
        )
        return 2
    return asyncio.run(run(args.variant, args.cases, args.reps, args.split))


if __name__ == "__main__":
    sys.exit(main())
