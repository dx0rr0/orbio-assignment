"""Talk to the engine in text mode, with the real LLM and an in-memory repository.

uv run --env-file .env python scripts/chat.py                 # interactive
uv run --env-file .env python scripts/chat.py "Hola" "Sí"     # scripted utterances
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime

from claims_agent.adapters.memory import InMemoryConversationRepository
from claims_agent.adapters.openrouter import OpenRouterChatModel
from claims_agent.domain import MADRID
from claims_agent.engine import ConversationEngine, RetryingChatModel, TurnResult
from claims_agent.seed import seed_policies


def show(result: TurnResult) -> None:
    for reply in result.replies:
        print(f"\nAGENTE> {reply.text}")


async def main(utterances: list[str]) -> None:
    repository = InMemoryConversationRepository(seed_policies(datetime.now(MADRID).date()))
    engine = ConversationEngine(
        llm=RetryingChatModel(OpenRouterChatModel.create(os.environ["OPENROUTER_API_KEY"])),
        conversations=repository,
        policies=repository,
        references=repository,
    )
    result = await engine.start()
    show(result)
    conversation_id = result.conversation.id

    lines = iter(utterances) if utterances else None
    while not result.conversation.claim.status.is_final:
        if lines is None:
            try:
                utterance = await asyncio.to_thread(input, "\nTÚ> ")
            except EOFError:
                break
        else:
            line = next(lines, None)
            if line is None:
                break
            utterance = line
            print(f"\nTÚ> {utterance}")
        result = await engine.handle_text(conversation_id, utterance)
        show(result)

    claim = result.conversation.claim
    print("\n--- claim ---")
    print(json.dumps(claim.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print(f"urgency: {claim.urgency}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main(sys.argv[1:]))
