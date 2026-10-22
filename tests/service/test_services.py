import pytest

from claims_agent.ports import LLMError, SpeechError
from claims_service.intake import services
from claims_service.intake.services import connections_warm_up


class Client:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.warm_ups = 0

    async def warm_up(self) -> None:
        self.warm_ups += 1
        if self.error is not None:
            raise self.error


async def test_a_failed_warm_up_is_logged_and_the_rest_still_warm_up(caplog):
    clients = [Client(SpeechError("down")), Client(), Client(LLMError("down"))]

    await connections_warm_up(*clients)()

    assert [client.warm_ups for client in clients] == [1, 1, 1]
    assert caplog.text.count("Could not warm up a provider connection") == 2


async def test_an_unexpected_error_is_not_hidden():
    with pytest.raises(ValueError, match="bug"):
        await connections_warm_up(Client(ValueError("bug")))()


def test_without_an_openrouter_key_the_service_does_not_start(settings):
    # Rather than start and apologise on every turn.
    settings.OPENROUTER_API_KEY = ""

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        services.for_consumer()


def test_without_an_openrouter_key_the_tasks_that_need_no_llm_still_run(settings):
    # Celery only logs an error raised while a worker process starts, and an unconfigured
    # worker would not sweep abandoned conversations either.
    settings.OPENROUTER_API_KEY = ""

    assert services.for_tasks().repository is not None
