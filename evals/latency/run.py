"""Time-to-first-audio eval: each case is one declarant turn through the real WebSocket
consumer, with the real STT, LLM and TTS on OpenRouter (see docs/LATENCY.md).

    uv run --env-file .env --env-file .env.eval python -m evals.latency.run --variant baseline
    uv run ... python -m evals.latency.run --variant v1 --split train
    uv run ... python -m evals.latency.run --variant v1 --cases R05 R10 --reps 2

The consumer runs with the production services, except for the repository and the access
tokens: the conversation each case starts from is put in memory, so nothing touches the
development database. Langfuse is off; everything is recorded under
`.claude/hillclimb/ttfa/<variant>/`: `results.jsonl` (one row per case and rep), `traces/`,
`errors.jsonl` and `summary.json`.

Spending is checked against the OpenRouter key before every case: the run stops once the key
has used BUDGET_STOP dollars. The key's own limit is the hard cap.
"""

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

# Langfuse off and dev defaults, before Django reads the settings.
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.setdefault("DJANGO_DEBUG", "true")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "claims_service.settings")

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
from claims_agent.ports import ChatModel, Completion
from claims_agent.seed import seed_policies
from evals.latency.cases import CASES, TEST_IDS, Case, read_back_text, resolve

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / ".claude" / "hillclimb" / "ttfa"
AUDIO = Path(__file__).parent / "audio"
HARNESS = [Path(__file__), Path(__file__).parent / "cases.py"]
BUDGET_STOP = 0.28
CASE_TIMEOUT_S = 90
# A cold first connection (imports, joining the Redis group) can take longer than the
# communicator's default of 1 s.
CONNECT_TIMEOUT_S = 10


# --- instrumentation ----------------------------------------------------------------------------


@dataclass
class Meter:
    llm_calls: list[dict[str, Any]] = field(default_factory=list)


class MeteredChatModel:
    """The production chat model, timing each completion."""

    def __init__(self, llm: ChatModel, meter: Meter) -> None:
        self._llm = llm
        self._meter = meter

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
    ) -> Completion:
        started = time.perf_counter()
        completion = await self._llm.complete(system=system, messages=messages, tools=tools)
        self._meter.llm_calls.append({"latency_s": round(time.perf_counter() - started, 3)})
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


def load_state() -> dict[str, Any]:
    path = FLOW / "_state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(state: dict[str, Any]) -> None:
    FLOW.mkdir(parents=True, exist_ok=True)
    (FLOW / "_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


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


def trace_of(conversation: Conversation) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for message in conversation.messages:
        if message.role is Role.TOOL:
            turns.append({"role": "tool_result", "content": message.content or ""})
            continue
        if message.content:
            turns.append({"role": message.role.value, "content": message.content})
        for call in message.tool_calls:
            turns.append({"role": "tool_call", "name": call.name, "content": call.arguments})
    return turns


@dataclass(frozen=True)
class Harness:
    """The consumer's application and what the cases share with it."""

    application: Any
    repository: InMemoryConversationRepository
    access: InMemoryAccess
    meter: Meter


async def run_case(case: Case, harness: Harness) -> dict[str, Any]:
    now = datetime.now(MADRID)
    conversation = await harness.repository.create(initial_conversation(case, now))
    token = await harness.access.issue_token(conversation.id)
    wav = (AUDIO / case.audio).read_bytes()
    meter = harness.meter
    meter.llm_calls.clear()

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
            event = await communicator.receive_json_from(timeout=CASE_TIMEOUT_S)
            kind = event["type"]
            marks.setdefault(kind, round(time.perf_counter() - started, 3))
            if kind == "transcript":
                transcript = event["text"]
                if not transcript:
                    raise RuntimeError("empty transcript (the STT failed)")
            elif kind == "bot_message":
                replies.append(event["text"])
            elif kind == "error":
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
        "llm_s": round(sum(c["latency_s"] for c in meter.llm_calls), 3),
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
    tokens, which live in memory with the conversations."""
    from claims_agent.engine import ConversationEngine
    from claims_service.intake import services
    from claims_service.intake.routing import websocket_application

    repository = InMemoryConversationRepository(seed_policies(datetime.now(MADRID).date()))
    access = InMemoryAccess()
    meter = Meter()
    telemetry = services.telemetry_from_settings()
    llm = MeteredChatModel(services.chat_model_from_settings(), meter)
    consumer_services = services.ConsumerServices(
        engine=ConversationEngine(llm=llm, repository=repository, telemetry=telemetry),
        voice=services.voice_from_settings(telemetry),
        repository=repository,
        telemetry=telemetry,
        access=access,
        request_summary=services.request_summary,
    )
    return Harness(websocket_application(consumer_services), repository, access, meter)


async def run(variant: str, case_ids: list[str] | None, reps: int, split: str = "all") -> int:
    import django

    django.setup()
    harness = build_harness()

    cases = [
        c
        for c in CASES
        if (case_ids is None or c.id in case_ids)
        and (split == "all" or (c.id in TEST_IDS) == (split == "test"))
    ]
    out = FLOW / variant
    (out / "traces").mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done.add((row["prompt_id"], row["rep"]))

    start_usage = key_usage()
    print(f"{variant}: {len(cases)} cases x {reps} reps, key usage ${start_usage:.4f}")
    for rep in range(reps):
        for case in cases:
            if (case.id, rep) in done:
                continue
            before = key_usage()
            if before >= BUDGET_STOP:
                print(f"budget stop: key usage ${before:.4f} >= ${BUDGET_STOP}")
                return 3
            try:
                row = await run_case(case, harness)
            except Exception as error:  # recorded, never scored
                with (out / "errors.jsonl").open("a", encoding="utf-8") as errors:
                    errors.write(
                        json.dumps(
                            {
                                "prompt_id": case.id,
                                "rep": rep,
                                "error": repr(error),
                                "llm_calls": list(harness.meter.llm_calls),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                # Infrastructure fails the same way for every case: stop before paying for more.
                print(f"  {case.id} rep{rep}: ERROR {error!r}; stopping the run")
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
    ran = sum(1 for rep in range(reps) for case in cases if (case.id, rep) not in done)
    summary: dict[str, Any] = {
        "spent_this_run_usd": round(spent, 6),
        "cost_per_case_usd": round(spent / ran, 6) if ran else None,
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
