class FieldError(ValueError):
    """A proposed value for a claim field is invalid; the message is shown to the LLM."""


class ClaimClosedError(RuntimeError):
    """The claim is in a final status and can no longer change."""


class IncompleteClaimError(ValueError):
    """A required field of the claim is missing where its value is needed."""


class InvalidTransitionError(RuntimeError):
    """The requested status change is not allowed from the claim's current status."""
