from collections.abc import Generator
from typing import Generic, TypeVar

from ._core import Coro, Event


_T = TypeVar("_T")


def as_completed(coros: set[Coro[_T]]) -> Coro["AsCompleted"]:
    """Schedules multiple coroutines, iterating through their outputs in the order that they complete.

    Usage is via `.done()` and `.get()` as follows:
    ```python
    import tinyio

    def sleep(x):
        yield tinyio.sleep(x)
        return x

    def as_completed_demo():
        iterator = yield tinyio.as_completed({sleep(7), sleep(2), sleep(4)})
        while not iterator.done():
            out = yield iterator.get()
            print(f"As completed demo: {out}")

    loop = tinyio.Loop()
    loop.run(as_completed_demo())
    # As completed demo: 2
    # As completed demo: 4
    # As completed demo: 7
    ```
    """
    if not isinstance(coros, set) or any(not isinstance(coro, Generator) for coro in coros):
        raise ValueError("`AsCompleted(coros=...)` must be a set of coroutines.")

    outs = {}
    put_count = 0
    events = [Event() for _ in coros]

    def wrapper(coro):
        nonlocal put_count
        out = yield coro
        outs[put_count] = out
        events[put_count].set()
        put_count += 1

    yield {wrapper(coro) for coro in coros}
    return AsCompleted(outs, events)


class AsCompleted(Generic[_T]):
    def __init__(self, outs: dict, events: list[Event]):
        self._get_count = 0
        self._outs = outs
        self._events = events

    def done(self) -> bool:
        """Whether all coroutines are being waited on. This does not imply that all coroutines have necessarily
        finished executing; it just implies that you should not call `.get()` any more times.
        """
        return self._get_count == len(self._events)

    def get(self) -> Coro[_T]:
        """Yields the output of the next coroutine to complete."""
        get_count = self._get_count
        if self._get_count >= len(self._events):
            raise RuntimeError(
                f"Called `AsCompleted.get` {self._get_count + 1} times, which is greater than the number of coroutines "
                f"which are being waited on ({len(self._events)})."
            )
        self._get_count += 1
        return self._get(get_count)

    def _get(self, get_count: int):
        yield from self._events[get_count].wait()
        return self._outs.pop(get_count)
