import queue
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, cast

from ._core import Coro, Event, Loop
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

    with Loop().runtime(coro, exception_group) as gen:
        while True:
            try:
                wait = next(gen)
            except StopIteration as e:
                return e.value
            if wait is None:
                await asyncio.sleep(0)
            else:
                await asyncio.get_running_loop().run_in_executor(None, wait)


def from_trio(async_fn: Callable[..., Any], *args: Any) -> Coro[_Return]:
    """Converts a `trio`-compatible async function into a `tinyio`-compatible coroutine.

    Uses trio's guest mode to run trio on top of the tinyio event loop.
    """
    import trio  # pyright: ignore[reportMissingImports]

    callback_queue: queue.Queue[Callable[[], Any]] = queue.Queue()
    event = Event()
    outcome_ref = [None]
    done_ref = [False]

    # I think this should be threadsafe as-is without any extra threading locks etc. necessary.
    def run_sync_soon_threadsafe(fn: Callable[[], Any]) -> None:
        callback_queue.put(fn)
        event.set()

    def done_callback(outcome: Any) -> None:
        outcome_ref[0] = outcome
        done_ref[0] = True
        event.set()

    trio.lowlevel.start_guest_run(
        async_fn,
        *args,
        run_sync_soon_threadsafe=run_sync_soon_threadsafe,
        done_callback=done_callback,
    )

    while not done_ref[0]:
        yield from event.wait()
        event.clear()
        while True:
            try:
                fn = callback_queue.get_nowait()
            except queue.Empty:
                break
            fn()

    # We're unwrapping an `outcome.Outcome` object.
    return cast(Any, outcome_ref[0]).unwrap()


async def to_trio(coro: Coro[_Return], exception_group: None | bool = None) -> _Return:
    """Converts a `tinyio`-compatible coroutine into a `trio`-compatible coroutine."""
    import trio  # pyright: ignore[reportMissingImports]

    with Loop().runtime(coro, exception_group) as gen:
        while True:
            try:
                wait = next(gen)
            except StopIteration as e:
                return e.value
            if wait is None:
                await trio.sleep(0)
            else:
                await trio.to_thread.run_sync(wait)


def nest(coro: Coro[_Return], exception_group: None | bool = None) -> Coro[_Return]:
    """Runs a coroutine in a separate "inner" loop.

    In particular, this isolates coroutines running in the "outer" loop from exceptions
    occurring from coroutines in the inner one, while still allowing corountines in both
    loops to make progress simultaneously.
    """
    with Loop().runtime(coro, exception_group) as gen:
        while True:
            try:
                wait = next(gen)
            except StopIteration as e:
                return e.value
            if wait is None:
                yield
            else:
                yield run_in_thread(wait)
