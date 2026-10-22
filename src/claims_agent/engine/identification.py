"""The claim's policy as the turn may use it: only once the declarant is identified by its
number and plate may the templates and the LLM's prompt see what it says."""

from claims_agent.domain import Claim, Policy, identified_policy
from claims_agent.ports import PolicyCatalog


async def policy_of(claim: Claim, policies: PolicyCatalog) -> Policy | None:
    """The claim's policy, if its number and plate identify the declarant."""
    if claim.policy_number is None or claim.plate is None:
        return None
    return identified_policy(claim, await policies.get_policy(claim.policy_number))
