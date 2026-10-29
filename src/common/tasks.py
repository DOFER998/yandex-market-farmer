import asyncio


async def settle[T](task: asyncio.Task[T]) -> tuple[T, bool]:
    """Wait for real completion despite repeated cancellation of the caller."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled
