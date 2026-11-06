"""Async variant with a trace continued from the caller's context."""

from __future__ import annotations

import asyncio
import os

from authgebra import AsyncClient, CheckRequest, Config, new_traceparent


async def main(subject_id: str) -> None:
    config = Config(
        base_url=os.environ["AUTHGEBRA_URL"],
        token_provider=lambda: os.environ["AUTHGEBRA_TOKEN"],
    )
    parent = new_traceparent()  # normally taken from your tracing library
    async with AsyncClient(config) as client:
        result = await client.authorization.check(
            CheckRequest(subject_id, "document:read"), traceparent=parent
        )
        print(result.decision.value, "trace", result.info.trace_id)


if __name__ == "__main__":
    asyncio.run(main(os.environ["SUBJECT_ID"]))
