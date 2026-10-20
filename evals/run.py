"""Evals: an LLM plays the declarant of each scenario against the real engine (text mode,
in-memory storage) and the final claim is checked against what the scenario expects.

    uv run --env-file .env python -m evals.run                    # all scenarios
    uv run --env-file .env python -m evals.run expired_policy     # some of them
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from claims_agent.adapters.memory import InMemoryConversationRepository
from claims_agent.adapters.openrouter import OpenRouterChatModel
from claims_agent.domain import MADRID, Claim, Message, Role
from claims_agent.engine import ConversationEngine
from claims_agent.engine.hallucinations import check_reply
from claims_agent.seed import seed_policies
from evals.scenarios import SCENARIOS, Scenario

REPORT = Path(__file__).parent / "report.json"

_DECLARANT_SYSTEM = """\
Estás llamando por teléfono a tu aseguradora para declarar un siniestro de coche. Hablas con \
un asistente virtual. Esta es tu situación; no inventes nada que no esté aquí y, si te \
preguntan algo que no sabes, dilo:

{persona}

Responde solo con lo que dirías en voz alta, en español de España, de forma natural y breve \
(una o dos frases). No des todos los datos de golpe: responde a lo que te pregunten. Cuando el \
asistente te lea los datos y te pregunte si son correctos, confírmalo si lo son o corrige lo \
que esté mal."""


@dataclass
class Result:
    scenario: str
    passed: bool
    failures: list[str]
    turns: int
    hallucination_findings: list[str]
    transcript: list[str]


class SimulatedDeclarant:
    def __init__(self, llm: OpenRouterChatModel, persona: str) -> None:
        self._llm = llm
        self._system = _DECLARANT_SYSTEM.format(persona=persona)
        # From the declarant's point of view the agent is the "user" it answers to.
        self._messages: list[Message] = []

    async def answer(self, agent_said: str) -> str:
        self._messages.append(Message(role=Role.USER, content=agent_said))
        completion = await self._llm.complete(
            system=self._system, messages=self._messages, tools=[]
        )
        reply = (completion.content or "").strip()
        self._messages.append(Message(role=Role.ASSISTANT, content=reply))
        return reply


def check(scenario: Scenario, claim: Claim) -> list[str]:
    failures = []
    if claim.status is not scenario.status:
        failures.append(f"status is {claim.status}, expected {scenario.status}")
    if scenario.closure_reason and claim.closure_reason is not scenario.closure_reason:
        failures.append(
            f"closure reason is {claim.closure_reason}, expected {scenario.closure_reason}"
        )
    for name, expected in scenario.expect.items():
        actual = getattr(claim, name)
        ok = expected(actual) if callable(expected) else actual == expected
        if not ok:
            wanted = getattr(expected, "__name__", repr(expected))
            failures.append(f"{name} is {actual!r}, expected {wanted}")
    return failures


async def run(scenario: Scenario, llm: OpenRouterChatModel) -> Result:
    repository = InMemoryConversationRepository(seed_policies(datetime.now(MADRID).date()))
    engine = ConversationEngine(llm=llm, repository=repository)
    declarant = SimulatedDeclarant(llm, scenario.persona)

    result = await engine.start()
    transcript, findings, turns = [], [], 0
    while not result.conversation.claim.status.is_final and turns < scenario.max_turns:
        agent_said = " ".join(reply.text for reply in result.replies)
        transcript.append(f"AGENTE: {agent_said}")
        utterance = await declarant.answer(agent_said)
        transcript.append(f"DECLARANTE: {utterance}")
        result = await engine.handle_text(result.conversation.id, utterance)
        turns += 1
        said_so_far = " ".join(t for t in transcript if t.startswith("DECLARANTE"))
        for reply in result.replies:
            if not reply.scripted:
                findings += check_reply(reply.text, result.conversation.claim, said_so_far)
    transcript.append("AGENTE: " + " ".join(reply.text for reply in result.replies))

    failures = check(scenario, result.conversation.claim)
    if turns >= scenario.max_turns and not result.conversation.claim.status.is_final:
        failures.append(f"still {result.conversation.claim.status} after {turns} turns")
    return Result(scenario.name, not failures, failures, turns, findings, transcript)


async def main(names: list[str]) -> int:
    llm = OpenRouterChatModel.create(os.environ["OPENROUTER_API_KEY"])
    scenarios = [s for s in SCENARIOS if not names or s.name in names]
    results = await asyncio.gather(*(run(s, llm) for s in scenarios))

    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        findings = len(r.hallucination_findings)
        print(f"{mark}  {r.scenario:<36} {r.turns:>2} turns  {findings} findings")
        for failure in r.failures:
            print(f"      - {failure}")
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed; transcripts in {REPORT}")
    REPORT.write_text(
        json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
