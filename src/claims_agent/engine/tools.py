"""The tools the LLM may call. They only propose: the engine validates and decides."""

from collections.abc import Mapping
from typing import Any

from claims_agent.domain import CLAIM_FIELDS, LossType
from claims_agent.ports import ToolSpec

UPDATE_CLAIM = "update_claim"
CONFIRM_CLAIM = "confirm_claim"
CONFIRM_VALUE = "declarant_confirms_value"
DECLARANT_REFUSES = "declarant_refuses_field"
# An argument of UPDATE_CLAIM that is not a claim field: the reply to say if every value is
# accepted, which saves asking the LLM again once the tool has run.
REPLY_IF_ACCEPTED = "reply_if_accepted"

_BOOL = {"type": "boolean"}
_TEXT = {"type": "string"}
_DATETIME = {
    "type": "string",
    "description": "ISO 8601 local datetime in Europe/Madrid, e.g. 2026-10-19T19:00",
}

# JSON Schema of each claim field.
_CLAIM_PROPERTIES: dict[str, dict[str, Any]] = {
    "policy_number": {**_TEXT, "description": "As said by the declarant, e.g. AUT-48213907"},
    "plate": {**_TEXT, "description": "Plate of the insured vehicle, e.g. 4721 KLM"},
    "declarant_name": {**_TEXT, "description": "Full name of the person talking to you"},
    "driver_is_policyholder": {
        **_BOOL,
        "description": "Whether the policyholder was driving the insured vehicle",
    },
    "driver_name": {**_TEXT, "description": "Full name of the driver, if not the policyholder"},
    "loss_type": {"type": "string", "enum": [t.value for t in LossType]},
    "loss_window": {
        "type": "object",
        "description": (
            "When it happened. Same start and end when the time is known; otherwise the "
            "interval it must have happened in (for theft or parked damage: from when the "
            "vehicle was last seen to when the damage was discovered)."
        ),
        "properties": {"start": _DATETIME, "end": _DATETIME},
        "required": ["start", "end"],
    },
    "loss_location": {
        "type": "object",
        "description": (
            "Where it happened, normalized: way (street, road or kilometre point, optional), "
            "municipality, province (one of Spain's 52) and country."
        ),
        "properties": {
            "way": _TEXT,
            "municipality": _TEXT,
            "province": _TEXT,
            "country": _TEXT,
        },
        "required": ["municipality", "province", "country"],
    },
    "description": {**_TEXT, "description": "What happened, in the declarant's words, summarized"},
    "injuries": {**_BOOL, "description": "Whether anyone was injured"},
    "vehicle_immobilized": {**_BOOL, "description": "Whether the insured vehicle cannot be driven"},
    "other_vehicle_involved": {**_BOOL, "description": "Collision only"},
    "other_vehicle_plate": {**_TEXT, "description": "Collision with another vehicle only"},
    "friendly_report_signed": {
        **_BOOL,
        "description": "Collision with another vehicle only: whether both drivers signed the "
        "friendly accident report (parte amistoso)",
    },
    "police_report_filed": {**_BOOL, "description": "Theft only: whether it was reported"},
}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Some models fill every property of a tool; null gives them a way to say "not stated"
    instead of inventing a value."""
    nullable = {**schema, "type": [schema["type"], "null"]}
    if "enum" in schema:
        nullable["enum"] = [*schema["enum"], None]
    return nullable


def stated_values(proposed: Mapping[str, Any]) -> dict[str, Any]:
    """The proposed values that carry something: null, blank text and objects with only blank
    members mean the declarant did not say it, not that they said something invalid."""

    def blank(value: Any) -> bool:
        if value is None or (isinstance(value, str) and not value.strip()):
            return True
        return isinstance(value, dict) and all(blank(v) for v in value.values())

    return {name: value for name, value in proposed.items() if not blank(value)}


TOOLS: tuple[ToolSpec, ...] = (
    {
        "type": "function",
        "function": {
            "name": UPDATE_CLAIM,
            "description": (
                "Record claim fields the declarant has just stated or corrected. Include only "
                "fields with values the declarant actually gave, and leave out (or set to null) "
                "every other field; never guess or fill in defaults. The result says which "
                "values were accepted and why others were rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    **{name: _nullable(s) for name, s in _CLAIM_PROPERTIES.items()},
                    REPLY_IF_ACCEPTED: {
                        "type": ["string", "null"],
                        "description": (
                            "What you would say next to the declarant if every value you send "
                            "is accepted: usually the question for the next missing field, plus "
                            "a short answer to anything they asked. It is said as written, "
                            "without asking you again. If any value is rejected it is not said, "
                            "and you get the result to reply to."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": CONFIRM_CLAIM,
            "description": (
                "Call when, after the read-back of the complete claim, the declarant says that "
                "everything is correct."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": CONFIRM_VALUE,
            "description": (
                "Call when the system asked the declarant to confirm a date or place that "
                "leaves the loss uncovered, and the declarant says it is correct."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": DECLARANT_REFUSES,
            "description": (
                "Call every time the declarant refuses to give a required field, including "
                "when they insist after you explained why it is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": list(CLAIM_FIELDS)}},
                "required": ["field"],
                "additionalProperties": False,
            },
        },
    },
)
