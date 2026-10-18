import asyncio

import pytest

from app.core.single_flight import SingleFlight


async def test_concurrent_calls_share_one_computation() -> None:
    flight: SingleFlight[int] = SingleFlight()
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return 42

    results = await asyncio.gather(*(flight.run("k", compute) for _ in range(10)))
    assert calls == 1
    assert [value for value, _ in results] == [42] * 10
    assert sum(shared for _, shared in results) == 9
    assert len(flight) == 0


async def test_different_keys_do_not_share() -> None:
    flight: SingleFlight[str] = SingleFlight()

    async def compute(value: str) -> str:
        await asyncio.sleep(0.01)
        return value

    a, b = await asyncio.gather(
        flight.run("a", lambda: compute("a")), flight.run("b", lambda: compute("b"))
    )
    assert (a, b) == (("a", False), ("b", False))


async def test_failure_reaches_followers_and_is_not_remembered() -> None:
    flight: SingleFlight[int] = SingleFlight()

    async def failing() -> int:
        await asyncio.sleep(0.02)
        raise RuntimeError("upstream down")

    results = await asyncio.gather(
        *(flight.run("k", failing) for _ in range(3)), return_exceptions=True
    )
    assert all(isinstance(r, RuntimeError) for r in results)

    async def working() -> int:
        return 7

    assert await flight.run("k", working) == (7, False)


async def test_cancelled_follower_does_not_cancel_leader() -> None:
    flight: SingleFlight[int] = SingleFlight()

    async def compute() -> int:
        await asyncio.sleep(0.05)
        return 1

    leader = asyncio.create_task(flight.run("k", compute))
    await asyncio.sleep(0)
    follower = asyncio.create_task(flight.run("k", compute))
    await asyncio.sleep(0.01)
    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower
    assert await leader == (1, False)
