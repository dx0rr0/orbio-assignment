"""When a turn must stop the LLM and speak a template instead, and which template it speaks
(ADR-0005): the read-back, the reference, a value that leaves the loss uncovered, a closing."""

from claims_agent.domain import Claim, ClaimStatus, Policy, identified_policy
from claims_agent.engine import templates
from claims_agent.engine.templates import BotMessage
from claims_agent.ports import PolicyCatalog


def needs_script(before: Claim, after: Claim) -> bool:
    """Whether the claim changed in a way only a template may announce."""
    if after.status.is_final:
        return True
    if after.coverage_issue is not None:
        return before.fingerprint_to_confirm != after.fingerprint_to_confirm
    return after.status is ClaimStatus.PENDING_CONFIRMATION and (
        before.status is not ClaimStatus.PENDING_CONFIRMATION
        or before.data_fingerprint != after.data_fingerprint
    )


async def script(claim: Claim, *, heard: Claim | None, policies: PolicyCatalog) -> BotMessage:
    """`heard` is the claim as last read back to the declarant, if it was."""
    if claim.status is ClaimStatus.CONFIRMED:
        if claim.reference is None:
            raise ValueError("a confirmed claim always has a reference")
        return templates.confirmed(claim.reference)
    if claim.status is ClaimStatus.PENDING_CONFIRMATION:
        if heard is None:
            return templates.read_back(claim)
        return templates.read_back_changes(heard, claim)
    if not claim.status.is_final:
        return templates.confirm_coverage_value(claim, await policy_of(claim, policies))
    return templates.closed(claim, await policy_of(claim, policies))


async def policy_of(claim: Claim, policies: PolicyCatalog) -> Policy | None:
    """The claim's policy, once the declarant is identified by its number and plate: only
    then may the templates and the LLM's prompt use what it says."""
    if claim.policy_number is None or claim.plate is None:
        return None
    return identified_policy(claim, await policies.get_policy(claim.policy_number))
