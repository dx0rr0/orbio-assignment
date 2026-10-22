import asyncio
import json

from claims_agent.domain import CLAIM_FIELDS, ClaimStatus, ClosureReason, Role, close
from claims_agent.engine import ConversationEngine, templates, tools
from claims_agent.engine.tools import CONFIRM_CLAIM, CONFIRM_VALUE, DECLARANT_REFUSES, UPDATE_CLAIM
from claims_agent.engine.turns import MAX_LLM_FAILURES, MAX_REPEAT_REQUESTS
from tests.engine.fakes import COMPLETE, IDENTIFICATION, LLM_DOWN, NEW_POLICY, call, calls, say


def texts(result) -> list[str]:
    return [reply.text for reply in result.replies]


def tool_results(llm_request) -> list[dict]:
    return [json.loads(m.content) for m in llm_request["messages"] if m.role is Role.TOOL]


# --- greeting and plain conversation ------------------------------------------------------


async def test_start_greets_and_stores_the_greeting(engine, repository):
    result = await engine.start()

    assert texts(result) == [templates.greeting().text]
    stored = await repository.get(result.conversation.id)
    assert [m.content for m in stored.transcript] == [templates.greeting().text]


async def test_llm_reply_is_sent_and_stored(engine, llm, conversation_id):
    llm.then(say("¿Me indica su número de póliza?"))

    result = await engine.handle_text(conversation_id, "Hola, quiero dar un parte")

    assert texts(result) == ["¿Me indica su número de póliza?"]
    assert result.replies[0].scripted is False
    assert [m.content for m in result.conversation.transcript][-2:] == [
        "Hola, quiero dar un parte",
        "¿Me indica su número de póliza?",
    ]


async def test_system_prompt_carries_the_claim_state_and_current_date(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, IDENTIFICATION), say("Gracias. ¿Cómo se llama?"))

    await engine.handle_text(conversation_id, "AUT 48213907, matrícula 4721 KLM")

    first, second = llm.requests
    assert "martes 2026-10-20 12:00" in first["system"]
    assert '"missing_fields_in_order": [\n    "policy_number"' in first["system"]
    assert '"policy_number": "AUT-48213907"' in second["system"]
    # The policyholder's name is only shared once the policy has been validated.
    assert '"policyholder_name": null' in first["system"]
    assert '"policyholder_name": "María López Fernández"' in second["system"]


async def test_policyholder_name_is_not_shared_with_a_plate_that_does_not_match(
    engine, llm, conversation_id
):
    wrong_plate = {"policy_number": "AUT-48213907", "plate": "9999 ZZZ"}
    llm.then(call(UPDATE_CLAIM, wrong_plate), say("La matrícula no coincide. ¿Me la repite?"))

    await engine.handle_text(conversation_id, "AUT 48213907, matrícula 9999 ZZZ")

    assert '"policyholder_name": null' in llm.requests[1]["system"]
    assert "María López Fernández" not in llm.requests[1]["system"]


# --- tools --------------------------------------------------------------------------------


async def test_a_reply_prepared_with_an_accepted_update_is_said_without_asking_the_llm_again(
    engine, llm, conversation_id
):
    prepared = IDENTIFICATION | {tools.REPLY_IF_ACCEPTED: "Gracias. ¿Cómo se llama?"}
    llm.then(call(UPDATE_CLAIM, prepared))

    result = await engine.handle_text(conversation_id, "AUT 48213907, matrícula 4721 KLM")

    assert texts(result) == ["Gracias. ¿Cómo se llama?"]
    assert result.replies[0].scripted is False
    assert len(llm.requests) == 1
    assert result.conversation.claim.policy_number == "AUT-48213907"
    assert result.conversation.transcript[-1].content == "Gracias. ¿Cómo se llama?"


async def test_a_prepared_reply_is_not_said_when_a_value_is_rejected(engine, llm, conversation_id):
    # It was written assuming the value would be accepted; the LLM must explain the problem.
    wrong_plate = {"policy_number": "AUT-48213907", "plate": "9999 ZZZ"}
    prepared = wrong_plate | {tools.REPLY_IF_ACCEPTED: "Gracias. ¿Cómo se llama?"}
    llm.then(call(UPDATE_CLAIM, prepared), say("La matrícula no coincide. ¿Me la repite?"))

    result = await engine.handle_text(conversation_id, "AUT 48213907, matrícula 9999 ZZZ")

    assert texts(result) == ["La matrícula no coincide. ¿Me la repite?"]
    assert len(llm.requests) == 2


async def test_the_prepared_reply_is_not_a_claim_field(engine, llm, conversation_id):
    prepared = IDENTIFICATION | {tools.REPLY_IF_ACCEPTED: "Gracias. ¿Cómo se llama?"}
    llm.then(call(UPDATE_CLAIM, prepared), say("unused"))

    result = await engine.handle_text(conversation_id, "AUT 48213907, matrícula 4721 KLM")

    assert result.conversation.claim.field_errors == {}


async def test_without_a_prepared_reply_the_llm_is_asked_for_one(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, IDENTIFICATION | {tools.REPLY_IF_ACCEPTED: " "}), say("¿Nombre?"))

    result = await engine.handle_text(conversation_id, "AUT 48213907, matrícula 4721 KLM")

    assert texts(result) == ["¿Nombre?"]
    assert len(llm.requests) == 2


async def test_a_prepared_reply_gives_way_to_the_script(engine, llm, conversation_id):
    # Completing the claim triggers the read-back from the template (ADR-0005).
    llm.then(call(UPDATE_CLAIM, COMPLETE | {tools.REPLY_IF_ACCEPTED: "Todo registrado."}))

    result = await engine.handle_text(conversation_id, "Todos los datos")

    assert "Todo registrado." not in texts(result)
    assert result.conversation.claim.status is ClaimStatus.PENDING_CONFIRMATION


async def test_accepted_and_rejected_values_are_reported_to_the_llm(engine, llm, conversation_id):
    llm.then(
        call(UPDATE_CLAIM, {"policy_number": "AUT-48213907", "plate": "9999 ZZZ"}),
        say("La matrícula no coincide con la póliza. ¿Puede repetirla?"),
    )

    result = await engine.handle_text(conversation_id, "AUT 48213907 y la 9999 ZZZ")

    [tool_result] = tool_results(llm.requests[1])
    assert tool_result["accepted"] == ["policy_number"]
    assert "does not match" in tool_result["rejected"]["plate"]
    assert result.conversation.claim.policy_number == "AUT-48213907"
    assert result.conversation.claim.plate is None


async def test_invalid_tool_arguments_are_reported_back(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, "{not json"), say("¿Me repite la póliza?"))

    result = await engine.handle_text(conversation_id, "AUT 48213907")

    assert tool_results(llm.requests[1]) == [{"error": "arguments must be a JSON object"}]
    assert texts(result) == ["¿Me repite la póliza?"]


async def test_unknown_tool_is_reported_back(engine, llm, conversation_id):
    llm.then(call("delete_everything"), say("¿En qué le puedo ayudar?"))

    await engine.handle_text(conversation_id, "Hola")

    assert tool_results(llm.requests[1]) == [{"error": "unknown tool delete_everything"}]


async def test_text_accompanying_tool_calls_is_not_said(engine, llm, conversation_id):
    llm.then(
        call(UPDATE_CLAIM, IDENTIFICATION, text="Perfecto, queda confirmado."),
        say("Gracias. ¿Cómo se llama?"),
    )

    result = await engine.handle_text(conversation_id, "AUT 48213907, 4721 KLM")

    assert texts(result) == ["Gracias. ¿Cómo se llama?"]
    assert "Perfecto, queda confirmado." not in [m.content for m in result.conversation.messages]


# --- confirmation -------------------------------------------------------------------------


async def test_complete_claim_is_read_back_by_code_not_by_the_llm(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, COMPLETE))

    result = await engine.handle_text(conversation_id, "Todos los datos")

    assert len(llm.requests) == 1  # no second call: the read-back is a template
    assert result.conversation.claim.status is ClaimStatus.PENDING_CONFIRMATION
    assert texts(result) == [templates.read_back(result.conversation.claim).text]
    assert "A, U, T, 48, 21, 39, 07" in result.replies[0].spoken


async def test_confirmation_assigns_a_reference_and_announces_it(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM))
    await engine.handle_text(conversation_id, "Todos los datos")

    result = await engine.handle_text(conversation_id, "Sí, todo correcto")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.CONFIRMED
    assert claim.reference == "SIN-2026-000001"
    assert texts(result) == [templates.confirmed("SIN-2026-000001").text]


async def test_confirming_an_incomplete_claim_is_refused(engine, llm, conversation_id):
    llm.then(call(CONFIRM_CLAIM), say("Antes necesito su número de póliza."))

    result = await engine.handle_text(conversation_id, "Confírmalo ya")

    [tool_result] = tool_results(llm.requests[1])
    assert tool_result["error"] == "the claim is not complete yet"
    assert result.conversation.claim.status is ClaimStatus.IN_PROGRESS


async def test_a_correction_while_pending_reads_back_only_what_changed(
    engine, llm, conversation_id
):
    correction = {"loss_window": {"start": "2026-10-19T20:00", "end": "2026-10-19T20:00"}}
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(UPDATE_CLAIM, correction))
    heard = (await engine.handle_text(conversation_id, "Todos los datos")).conversation.claim

    result = await engine.handle_text(conversation_id, "No, fue a las ocho")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.PENDING_CONFIRMATION
    assert texts(result) == [templates.read_back_changes(heard, claim).text]
    assert "a las 20:00" in result.replies[0].text
    assert result.conversation.read_back_claim == claim


async def test_a_claim_completed_again_after_a_correction_reads_back_only_what_changed(
    engine, llm, conversation_id
):
    # Another driver opens a required field: the claim goes back to in_progress.
    llm.then(
        call(UPDATE_CLAIM, COMPLETE),
        call(UPDATE_CLAIM, {"driver_is_policyholder": False}),
        say("¿Cómo se llama quien conducía?"),
        call(UPDATE_CLAIM, {"driver_name": "Laura López"}),
    )
    heard = (await engine.handle_text(conversation_id, "Todos los datos")).conversation.claim
    await engine.handle_text(conversation_id, "No conducía yo")

    result = await engine.handle_text(conversation_id, "Laura López")

    [reply] = texts(result)
    assert reply == (
        "He corregido este dato:\n- Conductor: Laura López.\nLo demás queda igual. "
        "¿Es todo correcto?"
    )
    assert result.conversation.claim.data_fingerprint != heard.data_fingerprint


async def test_completing_and_confirming_in_one_response_only_reads_back(
    engine, llm, conversation_id
):
    llm.then(calls(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM)))

    result = await engine.handle_text(conversation_id, "Ya está todo, confírmelo")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.PENDING_CONFIRMATION
    assert claim.reference is None
    assert texts(result) == [templates.read_back(claim).text]
    # Every tool call gets a result, so the history stays valid for the next LLM call.
    [_, skipped] = [m for m in result.conversation.messages if m.role is Role.TOOL]
    assert "error" in json.loads(skipped.content)


async def test_correcting_and_confirming_in_one_response_reads_back_again(
    engine, llm, conversation_id
):
    correction = {"loss_window": {"start": "2026-10-19T20:00", "end": "2026-10-19T20:00"}}
    llm.then(
        call(UPDATE_CLAIM, COMPLETE), calls(call(UPDATE_CLAIM, correction), call(CONFIRM_CLAIM))
    )
    heard = (await engine.handle_text(conversation_id, "Todos los datos")).conversation.claim

    result = await engine.handle_text(conversation_id, "Fue a las ocho, confírmelo")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.PENDING_CONFIRMATION
    assert texts(result) == [templates.read_back_changes(heard, claim).text]


async def test_confirming_data_that_was_not_read_back_is_refused(
    engine, llm, repository, conversation_id
):
    # Whatever path got unheard data into a pending claim, it cannot be confirmed.
    llm.then(call(UPDATE_CLAIM, COMPLETE))
    await engine.handle_text(conversation_id, "Todos los datos")
    conversation = await repository.get(conversation_id)
    unheard = conversation.claim.model_copy(update={"description": "Otra cosa que nadie oyó"})
    await repository.save(conversation.model_copy(update={"claim": unheard}))
    llm.then(call(CONFIRM_CLAIM), say("Antes le leo los datos de nuevo."))

    result = await engine.handle_text(conversation_id, "Sí, confírmelo")

    assert result.conversation.claim.status is ClaimStatus.PENDING_CONFIRMATION
    assert "error" in tool_results(llm.requests[-1])[-1]


async def test_no_answer_to_the_read_back_lets_the_llm_ask_what_to_change(
    engine, llm, conversation_id
):
    llm.then(call(UPDATE_CLAIM, COMPLETE), say("¿Qué dato quiere corregir?"))
    await engine.handle_text(conversation_id, "Todos los datos")

    result = await engine.handle_text(conversation_id, "No")

    assert texts(result) == ["¿Qué dato quiere corregir?"]


# --- closing by code ----------------------------------------------------------------------


LAST_WEEK = {
    "policy_number": NEW_POLICY.number,
    "plate": NEW_POLICY.plate,
    "loss_window": {"start": "2026-10-13T22:00", "end": "2026-10-14T08:00"},
}


async def test_a_value_outside_the_coverage_period_is_stated_back_for_confirmation(
    engine, llm, conversation_id
):
    llm.then(call(UPDATE_CLAIM, LAST_WEEK))

    result = await engine.handle_text(conversation_id, "Me robaron el coche la semana pasada")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.IN_PROGRESS
    assert claim.coverage_issue is ClosureReason.OUTSIDE_COVERAGE_PERIOD
    assert len(llm.requests) == 1  # the question is a template
    assert texts(result) == [templates.confirm_coverage_value(claim, NEW_POLICY).text]
    assert "13 de octubre de 2026" in result.replies[0].text
    assert "17 de octubre de 2026" in result.replies[0].text


async def test_a_confirmed_value_outside_the_coverage_period_closes_the_claim(
    engine, llm, conversation_id
):
    llm.then(call(UPDATE_CLAIM, LAST_WEEK), call(CONFIRM_VALUE))
    await engine.handle_text(conversation_id, "Me robaron el coche la semana pasada")

    result = await engine.handle_text(conversation_id, "Sí, fue el martes 13")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.NOT_COVERED
    assert claim.closure_reason is ClosureReason.OUTSIDE_COVERAGE_PERIOD
    assert texts(result) == [templates.closed(claim, NEW_POLICY).text]


async def test_a_corrected_value_reopens_the_flow(engine, llm, conversation_id):
    correction = {"loss_window": {"start": "2026-10-19T22:00", "end": "2026-10-20T08:00"}}
    llm.then(
        call(UPDATE_CLAIM, LAST_WEEK),
        call(UPDATE_CLAIM, correction),
        say("Gracias. ¿Cómo se llama?"),
    )
    await engine.handle_text(conversation_id, "Me robaron el coche la semana pasada")

    result = await engine.handle_text(conversation_id, "No, perdone, fue anoche")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.IN_PROGRESS
    assert claim.coverage_issue is None
    assert texts(result) == ["Gracias. ¿Cómo se llama?"]


async def test_a_value_cannot_be_confirmed_in_the_response_that_flags_it(
    engine, llm, conversation_id
):
    llm.then(calls(call(UPDATE_CLAIM, LAST_WEEK), call(CONFIRM_VALUE)))

    result = await engine.handle_text(conversation_id, "La semana pasada, y sí, es correcto")

    assert result.conversation.claim.status is ClaimStatus.IN_PROGRESS


async def test_confirming_a_value_when_none_is_flagged_is_refused(engine, llm, conversation_id):
    llm.then(call(CONFIRM_VALUE), say("¿Me indica su número de póliza?"))

    await engine.handle_text(conversation_id, "Sí")

    assert "error" in tool_results(llm.requests[1])[0]


async def test_second_refusal_closes_the_claim_as_incomplete(engine, llm, conversation_id):
    llm.then(
        call(DECLARANT_REFUSES, {"field": "declarant_name"}),
        say("Lo necesito para identificar la declaración. ¿Me lo indica?"),
        call(DECLARANT_REFUSES, {"field": "declarant_name"}),
    )
    first = await engine.handle_text(conversation_id, "No le voy a decir mi nombre")
    assert first.conversation.claim.status is ClaimStatus.IN_PROGRESS
    assert "instruction" in tool_results(llm.requests[1])[0]

    result = await engine.handle_text(conversation_id, "Que no")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.INCOMPLETE
    assert claim.closure_reason is ClosureReason.DATA_REFUSED
    assert texts(result) == [templates.closed(claim, None).text]


async def test_refusing_a_field_that_is_not_missing_is_an_error(engine, llm, conversation_id):
    llm.then(call(DECLARANT_REFUSES, {"field": "driver_name"}), say("De acuerdo."))

    await engine.handle_text(conversation_id, "No quiero decirlo")

    assert "error" in tool_results(llm.requests[1])[0]


async def test_closed_conversation_does_not_call_the_llm(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM))
    await engine.handle_text(conversation_id, "Todos los datos")
    await engine.handle_text(conversation_id, "Sí")
    calls_before = len(llm.requests)

    result = await engine.handle_text(conversation_id, "¿Hola?")

    assert len(llm.requests) == calls_before
    assert "SIN-2026-000001" in texts(result)[0]


async def test_injuries_get_the_112_advice_before_the_llm_reply(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, {"injuries": True}), say("Lo siento mucho. ¿Dónde ocurrió?"))

    result = await engine.handle_text(conversation_id, "Mi hija se ha hecho daño")

    assert texts(result) == [templates.emergency_advice().text, "Lo siento mucho. ¿Dónde ocurrió?"]
    contents = [m.content for m in result.conversation.transcript]
    assert contents[-2:] == texts(result)


# --- errors -------------------------------------------------------------------------------


async def test_unintelligible_utterance_asks_to_repeat_without_the_llm(
    engine, llm, conversation_id
):
    result = await engine.handle_text(conversation_id, " ... ")

    assert llm.requests == []
    assert texts(result) == [templates.ask_to_repeat().text]
    assert result.conversation.unintelligible_streak == 1


async def test_repeated_unintelligible_utterances_escalate(engine, conversation_id):
    for _ in range(MAX_REPEAT_REQUESTS):
        result = await engine.handle_text(conversation_id, "")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.ESCALATED
    assert claim.closure_reason is ClosureReason.RETRIES_EXHAUSTED


async def test_transient_llm_errors_are_retried(engine, llm, conversation_id):
    llm.then(LLM_DOWN, LLM_DOWN, say("¿Me indica su póliza?"))

    result = await engine.handle_text(conversation_id, "Hola")

    assert texts(result) == ["¿Me indica su póliza?"]


async def test_persistent_llm_failure_apologises_and_keeps_the_conversation(
    engine, llm, repository, conversation_id
):
    llm.then(LLM_DOWN, LLM_DOWN, LLM_DOWN)

    result = await engine.handle_text(conversation_id, "Hola, quiero dar un parte")

    assert texts(result) == [templates.technical_problem().text]
    stored = await repository.get(conversation_id)
    assert "Hola, quiero dar un parte" in [m.content for m in stored.transcript]
    assert stored.claim.status is ClaimStatus.IN_PROGRESS


async def test_claim_updates_survive_an_llm_failure_after_the_tool_ran(
    engine, llm, conversation_id
):
    llm.then(call(UPDATE_CLAIM, IDENTIFICATION), LLM_DOWN, LLM_DOWN, LLM_DOWN)

    result = await engine.handle_text(conversation_id, "AUT 48213907, 4721 KLM")

    assert result.conversation.claim.policy_number == "AUT-48213907"
    assert texts(result) == [templates.technical_problem().text]


async def test_repeated_llm_failures_escalate_as_a_system_error(engine, llm, conversation_id):
    llm.then(*[LLM_DOWN] * 3 * MAX_LLM_FAILURES)

    for _ in range(MAX_LLM_FAILURES):
        result = await engine.handle_text(conversation_id, "Hola")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.ESCALATED
    assert claim.closure_reason is ClosureReason.SYSTEM_ERROR


async def test_empty_llm_reply_counts_as_a_failure(engine, llm, conversation_id):
    llm.then(say("  "))

    result = await engine.handle_text(conversation_id, "Hola")

    assert texts(result) == [templates.technical_problem().text]
    assert result.conversation.llm_failure_streak == 1


async def test_llm_stuck_calling_tools_counts_as_a_failure(engine, llm, conversation_id):
    llm.then(*[call(UPDATE_CLAIM, {"declarant_name": "Ana"})] * 4)

    result = await engine.handle_text(conversation_id, "Soy Ana")

    assert texts(result) == [templates.technical_problem().text]


# --- hallucination checks -----------------------------------------------------------------


async def test_clean_reply_scores_one(engine, llm, telemetry, conversation_id):
    llm.then(say("¿Me indica su número de póliza?"))

    await engine.handle_text(conversation_id, "Hola")

    assert telemetry.scores == [("hallucination_check", 1.0, None)]


async def test_reply_with_an_invented_reference_scores_zero(
    engine, llm, telemetry, conversation_id
):
    llm.then(say("Su parte es el SIN-2026-123456."))

    result = await engine.handle_text(conversation_id, "Hola")

    assert texts(result) == ["Su parte es el SIN-2026-123456."]  # detected, not blocked
    [(name, value, comment)] = telemetry.scores
    assert (name, value) == ("hallucination_check", 0.0)
    assert "reference" in comment


# --- models that fill every tool property -------------------------------------------------


async def test_blank_proposals_are_not_stated_values(engine, llm, conversation_id):
    # Shape of a real completion: every property sent, the unstated ones blank or null.
    proposed = IDENTIFICATION | {
        "declarant_name": "Elena Sánchez",
        "driver_name": "",
        "loss_type": None,
        "loss_window": {"start": "", "end": ""},
        "loss_location": {"way": "", "municipality": "", "province": "", "country": ""},
        "description": "   ",
        "injuries": None,
    }
    llm.then(call(UPDATE_CLAIM, proposed), say("Gracias, Elena. ¿Quién conducía?"))

    result = await engine.handle_text(conversation_id, "Soy Elena, AUT 48213907, 4721 KLM")

    [tool_result] = tool_results(llm.requests[1])
    assert tool_result["accepted"] == ["policy_number", "plate", "declarant_name"]
    assert tool_result["rejected"] == {}
    claim = result.conversation.claim
    assert claim.field_errors == {}
    assert claim.failed_attempts == {}
    assert claim.injuries is None


def test_the_update_tool_has_a_property_for_every_claim_field_and_the_prepared_reply():
    [update] = [t for t in tools.TOOLS if t["function"]["name"] == UPDATE_CLAIM]
    properties = set(update["function"]["parameters"]["properties"])
    assert properties == {*CLAIM_FIELDS, tools.REPLY_IF_ACCEPTED}


def test_every_claim_field_can_be_declared_unstated():
    [update] = [t for t in tools.TOOLS if t["function"]["name"] == UPDATE_CLAIM]
    properties = update["function"]["parameters"]["properties"]
    assert all("null" in schema["type"] for schema in properties.values())
    assert None in properties["loss_type"]["enum"]


def test_stated_values_keep_falsy_answers():
    assert tools.stated_values({"injuries": False, "plate": "0", "x": None, "y": {"a": " "}}) == {
        "injuries": False,
        "plate": "0",
    }


async def test_validation_is_traced_with_the_stated_values(engine, llm, telemetry, conversation_id):
    llm.then(call(UPDATE_CLAIM, IDENTIFICATION | {"injuries": None}), say("¿Cómo se llama?"))

    await engine.handle_text(conversation_id, "AUT 48213907, 4721 KLM")

    assert telemetry.spans == [("validation", IDENTIFICATION)]


# --- counting per declarant turn ----------------------------------------------------------


async def test_a_value_retried_within_one_turn_fails_once(engine, llm, conversation_id):
    bad_plate = call(UPDATE_CLAIM, IDENTIFICATION | {"plate": "9999 ZZZ"})
    llm.then(bad_plate, bad_plate, bad_plate, say("La matrícula no coincide. ¿Me la repite?"))

    result = await engine.handle_text(conversation_id, "AUT 48213907, la 9999 ZZZ")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.IN_PROGRESS
    assert claim.failed_attempts == {"plate": 1}


async def test_two_refusals_in_one_response_count_once(engine, llm, conversation_id):
    refusal = call(DECLARANT_REFUSES, {"field": "declarant_name"})
    llm.then(calls(refusal, refusal), say("Lo necesito para tramitar el parte. ¿Me lo dice?"))

    result = await engine.handle_text(conversation_id, "No le voy a decir mi nombre")

    assert result.conversation.claim.status is ClaimStatus.IN_PROGRESS
    assert result.conversation.refusals == {"declarant_name": 1}
    assert texts(result) == ["Lo necesito para tramitar el parte. ¿Me lo dice?"]


async def test_a_conversation_closed_while_the_turn_ran_is_announced_as_closed(
    engine, llm, repository, conversation_id
):
    scripted_complete = llm.complete

    async def closed_meanwhile(**request):
        stored = await repository.get(conversation_id)
        claim = close(stored.claim, ClaimStatus.INCOMPLETE, ClosureReason.ABANDONED)
        await repository.save(stored.model_copy(update={"claim": claim}))
        return await scripted_complete(**request)

    llm.complete = closed_meanwhile
    llm.then(say("¿Me indica su número de póliza?"))

    result = await engine.handle_text(conversation_id, "Hola")

    claim = result.conversation.claim
    assert claim.status is ClaimStatus.INCOMPLETE
    assert texts(result) == [templates.already_closed(claim).text]


async def test_a_turn_that_takes_too_long_apologises(llm, repository, conversation_id):
    class HangingLLM:
        async def complete(self, **request):
            await asyncio.Event().wait()  # never answers

    engine = ConversationEngine(llm=HangingLLM(), repository=repository, turn_deadline=0.05)

    result = await engine.handle_text(conversation_id, "Hola, quiero dar un parte")

    assert texts(result) == [templates.technical_problem().text]
    assert result.conversation.llm_failure_streak == 1
    stored = await repository.get(conversation_id)
    assert "Hola, quiero dar un parte" in [m.content for m in stored.transcript]


async def test_tool_results_survive_the_turn_deadline(llm, repository, conversation_id):
    class SlowAfterTools:
        calls = 0

        async def complete(self, **request):
            self.calls += 1
            if self.calls > 1:
                await asyncio.Event().wait()  # never answers again
            return call(UPDATE_CLAIM, IDENTIFICATION)

    engine = ConversationEngine(llm=SlowAfterTools(), repository=repository, turn_deadline=0.05)

    result = await engine.handle_text(conversation_id, "AUT 48213907, 4721 KLM")

    assert result.conversation.claim.policy_number == "AUT-48213907"
    assert texts(result) == [templates.technical_problem().text]


async def test_a_one_character_answer_reaches_the_llm(engine, llm, conversation_id):
    llm.then(say("Gracias. ¿En qué municipio?"))

    result = await engine.handle_text(conversation_id, "8")

    assert texts(result) == ["Gracias. ¿En qué municipio?"]
    assert result.conversation.unintelligible_streak == 0
