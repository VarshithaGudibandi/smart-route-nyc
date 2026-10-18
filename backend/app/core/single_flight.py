"""Share one in-progress computation among concurrent identical requests."""

import asyncio
from collections.abc import Awaitable, Callable


class SingleFlight[T]:
    """Coalesces concurrent calls with the same key within this process.

    The first caller (the leader) runs the computation; callers arriving while
    it is in progress await the same result instead of repeating the work.
    Failures are shared too: followers arrived during the same failing call,
    and repeating it immediately would only add load to a struggling upstream.
    """

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future[T]] = {}

    async def run(self, key: str, compute: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Return ``(result, shared)``; ``shared`` is True for followers."""
        if (existing := self._inflight.get(key)) is not None:
            return await asyncio.shield(existing), True

        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            result = await compute()
        except BaseException as exc:
            future.set_exception(exc)
            # Mark the exception as retrieved when nobody else awaited it.
            future.exception()
            raise
        else:
            future.set_result(result)
            return result, False
        finally:
            del self._inflight[key]

    def __len__(self) -> int:
        return len(self._inflight)
