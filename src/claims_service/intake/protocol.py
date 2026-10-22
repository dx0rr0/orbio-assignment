"""The messages a client sends over the conversation WebSocket (see `consumers`), as
`ClientMessage` in frontend/src/conversation/protocol.ts. The server's events are built in
`presenters`."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class _Message(BaseModel):
    # Strict, as the frontend's types are: a number is no token, nor a string a flag.
    model_config = ConfigDict(strict=True, frozen=True)


class ResumeMessage(_Message):
    """The first message of a connection that resumes a conversation."""

    type: Literal["resume"]
    token: str


class AudioMessage(_Message):
    """A spoken utterance: a WAV or Ogg Opus file, base64-encoded."""

    type: Literal["audio"]
    data: str


class TextMessage(_Message):
    """A typed utterance (dev mode, evals), answered with speech only if `reply_audio`."""

    type: Literal["text"]
    text: str
    reply_audio: bool = False


ClientMessage = Annotated[ResumeMessage | AudioMessage | TextMessage, Field(discriminator="type")]
_client_message: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(content: object) -> ClientMessage | None:
    """The message in a frame's decoded JSON, or None if it is not one."""
    try:
        return _client_message.validate_python(content)
    except ValidationError:
        return None
