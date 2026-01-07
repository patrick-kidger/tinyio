from collections.abc import Coroutine
from typing import Any, TypeVar

from ._core import Coro, Loop
from ._thread import run_in_thread


_Return = TypeVar("_Return")


def from_asyncio(coro: Coroutine[Any, Any, _Return]) -> Coro[_Return]:
    """Converts an `asyncio`-compatible coroutine into a `tinyio`-compatible coroutine."""

    # This is a bit disappointing. Whilst `asyncio` does provide a hacky way to single-step its loop
    # (`loop.call_soon(loop.stop); loop.run_forever()`), it does *not* provide a method for checking when its event loop
    # needs to wake up. So continuously single-stepping the asyncio event loop is basically just busy-polling.
    #
    # Instead we just punt the whole thing into a thread.
    #
    # On a related note, it doesn't seem like there's any good way to nest asyncio -> tinyio -> asyncio.
    def run():
        import asyncio

        return asyncio.new_event_loop().run_until_complete(coro)

    return (yield run_in_thread(run))


async def to_asyncio(coro: Coro[_Return], exception_group: None | bool = None) -> Coroutine[Any, Any, _Return]:
    """Converts a `tinyio`-compatible coroutine into an `asyncio`-compatible coroutine."""
    import asyncio

    gen = Loop().runtime(coro, exception_group)
    while True:
        try:
            wait = next(gen)
        except StopIteration as e:
            return e.value
        if wait is None:
            await asyncio.sleep(0)
        else:
            await asyncio.get_running_loop().run_in_executor(None, wait)
