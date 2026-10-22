import asyncio

from asgiref.testing import ApplicationCommunicator

from claims_agent.engine import templates
from claims_service.intake.routing import lifespan_application
from tests.service.conftest import FAKE_PCM


async def test_the_services_are_closed_when_the_server_shuts_down(services):
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    services.resources.push_async_callback(close)
    communicator = ApplicationCommunicator(lifespan_application(services), {"type": "lifespan"})

    await communicator.send_input({"type": "lifespan.startup"})
    assert await communicator.receive_output() == {"type": "lifespan.startup.complete"}
    assert closed == []
    await communicator.send_input({"type": "lifespan.shutdown"})
    assert await communicator.receive_output() == {"type": "lifespan.shutdown.complete"}

    assert closed == [True]


async def test_the_greeting_is_synthesized_when_the_server_starts(services, tts):
    communicator = ApplicationCommunicator(lifespan_application(services), {"type": "lifespan"})

    await communicator.send_input({"type": "lifespan.startup"})
    assert await communicator.receive_output() == {"type": "lifespan.startup.complete"}
    for _ in range(50):
        if tts.spoken:
            break
        await asyncio.sleep(0.01)
    assert tts.spoken == [templates.greeting().spoken]  # before any conversation
    replayed = [piece async for piece in services.voice.speak([templates.greeting()])]
    await communicator.send_input({"type": "lifespan.shutdown"})
    assert await communicator.receive_output() == {"type": "lifespan.shutdown.complete"}

    assert tts.spoken == [templates.greeting().spoken]
    assert replayed == [FAKE_PCM]
