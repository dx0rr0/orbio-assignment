"""End to end: recorded utterances go over the WebSocket of the running stack (docker compose
up) and through the real STT, LLM and TTS; the final claim is checked.

    uv run pytest -m e2e            # E2E_WS_URL defaults to the compose frontend
"""

import asyncio
import base64
import json
import os
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

pytestmark = pytest.mark.e2e

AUDIO = Path(__file__).parent / "fixtures" / "audio"
WS_URL = os.environ.get("E2E_WS_URL", "ws://localhost:8080/ws/conversations/")
# Seconds for one turn: STT + LLM (with retries) + TTS.
TURN_TIMEOUT = 90


async def receive(ws) -> dict:
    async with asyncio.timeout(TURN_TIMEOUT):
        return json.loads(await ws.recv(decode=True))


async def turn(ws) -> list[dict]:
    """Events until the turn's claim update, plus the reply audio that follows it."""
    events = [await receive(ws)]
    while events[-1]["type"] != "claim_updated":
        events.append(await receive(ws))
    events.append(await receive(ws))  # every turn of a voice conversation is spoken back
    return events


async def run_scenario(utterances: list[str]) -> list[dict]:
    async with connect(WS_URL, max_size=None, open_timeout=10) as ws:
        assert (await receive(ws))["type"] == "conversation_started"
        events = await turn(ws)  # greeting
        for name in utterances:
            wav = (AUDIO / f"{name}.wav").read_bytes()
            await ws.send(json.dumps({"type": "audio", "data": base64.b64encode(wav).decode()}))
            events += await turn(ws)
    return events


def final_claim(events: list[dict]) -> dict:
    return next(e for e in reversed(events) if e["type"] == "claim_updated")["claim"]


def field(claim: dict, name: str):
    return next(f["value"] for f in claim["fields"] if f["name"] == name)


async def test_happy_path_broken_glass():
    events = await run_scenario(["1a", "1b", "1c"])

    claim = final_claim(events)
    assert claim["status"] == "confirmed"
    assert claim["reference"].startswith("SIN-")
    assert claim["urgency"] == "low"
    assert field(claim, "policy_number") == "AUT-48213907"
    assert field(claim, "loss_type") == "broken_glass"
    assert field(claim, "driver_is_policyholder") is True
    assert field(claim, "loss_location")["municipality"] == "Arganda del Rey"
    # Every turn was spoken back.
    assert sum(e["type"] == "audio" for e in events) == 4
    transcripts = [e["text"] for e in events if e["type"] == "transcript"]
    assert "AUT48213907" in transcripts[0].replace("-", "").replace(" ", "")


async def test_collision_with_another_driver_and_injuries():
    events = await run_scenario(["2a", "2b", "2c", "2d"])

    claim = final_claim(events)
    assert claim["status"] == "confirmed"
    assert claim["urgency"] == "high"
    assert field(claim, "driver_is_policyholder") is False
    assert field(claim, "driver_name").startswith("Laura")
    assert field(claim, "other_vehicle_plate") == "2548MNP"
    assert field(claim, "friendly_report_signed") is True
    bot_messages = [e["text"] for e in events if e["type"] == "bot_message"]
    assert any("112" in text for text in bot_messages)


async def test_theft_before_the_policy_started_is_not_covered():
    events = await run_scenario(["3a", "3b"])

    claim = final_claim(events)
    assert claim["status"] == "not_covered"
    assert claim["closure_reason"] == "outside_coverage_period"
    assert field(claim, "loss_type") == "theft"
