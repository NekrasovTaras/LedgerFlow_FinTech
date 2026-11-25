"""Run publisher, inbox consumer and webhook relay with restart-safe leases."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.observability import configure_logging
from app.infrastructure.database import engine
from app.modules.outbox.application import consumer_forever, publish_batch
from app.modules.webhooks.application import deliver_batch

logger = logging.getLogger("ledgerflow.workers")


async def relay_loop(action: Callable[[], Awaitable[Any]]) -> None:
    while True:
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("relay.retry type=%s", type(error).__name__)
        await asyncio.sleep(1)


async def main() -> None:
    configure_logging()
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(relay_loop(publish_batch))
            group.create_task(consumer_forever())
            group.create_task(relay_loop(deliver_batch))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
