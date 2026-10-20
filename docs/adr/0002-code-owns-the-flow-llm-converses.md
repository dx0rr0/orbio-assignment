# Code owns the flow; the LLM converses and extracts

On each turn the LLM converses freely and proposes field values through a tool call. Python validates each field and holds the claim state. Python also decides when the claim is complete, what its urgency is and when to move to confirmation. We rejected letting the LLM decide when the interview ends (non-deterministic, hard to test) and a state machine that asks field by field (a rigid conversation). This way validation, completeness and urgency are testable without an LLM.
