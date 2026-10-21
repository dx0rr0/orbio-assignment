from asgiref.testing import ApplicationCommunicator

from claims_service.intake.routing import lifespan_application


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
