"""Shared async test helper: wait in scheduling rounds, never in seconds."""

import asyncio


async def settle(rounds: int = 64) -> None:
    """Give every agent task ``rounds`` turns to drain its mailbox.

    One manager's full negotiation is at most 5 CFP rounds of a 6-yield bid
    window, so 64 rounds is enough for anything a test sets up, and the
    result does not depend on how fast the machine is.
    """
    for _ in range(rounds):
        await asyncio.sleep(0)
