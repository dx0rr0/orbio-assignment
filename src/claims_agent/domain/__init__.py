"""Pure domain model of an auto claim: no I/O, no framework, no LLM."""

from claims_agent.domain.claim import (
    CLAIM_FIELDS,
    Claim,
    ClaimStatus,
    ClosureReason,
    CompleteClaim,
    CoverageIssue,
    LossType,
    PendingField,
    Urgency,
    format_reference,
)
from claims_agent.domain.conversation import ClaimSummary, Conversation, Message, Role, ToolCall
from claims_agent.domain.errors import (
    ClaimClosedError,
    FieldError,
    IncompleteClaimError,
    InvalidTransitionError,
)
from claims_agent.domain.lifecycle import (
    MAX_FAILED_ATTEMPTS,
    UpdateResult,
    apply_update,
    close,
    confirm,
    confirm_value,
    identified_policy,
    policy_number_to_look_up,
)
from claims_agent.domain.location import LossLocation
from claims_agent.domain.loss_window import MADRID, Coverage, LossWindow, coverage
from claims_agent.domain.policy import Policy

__all__ = [
    "CLAIM_FIELDS",
    "MADRID",
    "MAX_FAILED_ATTEMPTS",
    "Claim",
    "ClaimClosedError",
    "ClaimStatus",
    "ClaimSummary",
    "ClosureReason",
    "CompleteClaim",
    "Conversation",
    "Coverage",
    "CoverageIssue",
    "FieldError",
    "IncompleteClaimError",
    "InvalidTransitionError",
    "LossLocation",
    "LossType",
    "LossWindow",
    "Message",
    "PendingField",
    "Policy",
    "Role",
    "ToolCall",
    "UpdateResult",
    "Urgency",
    "apply_update",
    "close",
    "confirm",
    "confirm_value",
    "coverage",
    "format_reference",
    "identified_policy",
    "policy_number_to_look_up",
]
