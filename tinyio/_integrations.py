import contextlib
import queue
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar, cast

from ._core import Coro, Event, Loop
from ._thread import run_in_thread


_Return = TypeVar("_Return")


def from_asyncio(coro: Coroutine[Any, Any, _Return]) -> Coro[_Return]:
    """Converts an `asyncio` coroutine into a `tinyio` coroutine.

    **Arguments:**

    - `coro`: an `asyncio` coroutine.

    **Returns:**

    A `tinyio` coroutine.

    !!! Example

        ```python
        async def add_one(x):
            await asyncio.sleep(1)
            return x + 1

        def foo(x):
            y = yield tinyio.from_asyncio(add_one(x))
            return y

        tinyio.Loop().run(foo(3))
        ```

    !!! warning

        Note that the entire asyncio event loop will be ran in a thread. This may lead to surprises if the `asyncio` and
        non-`asyncio` portions interact in non-threadsafe ways.
    """

    # This is a bit disappointing. Whilst `asyncio` does provide a hacky way to single-step its loop
    # (`loop.call_soon(loop.stop); loop.run_forever()`), it does *not* provide a method for checking when its event loop
    # needs to wake up. So continuously single-stepping the asyncio event loop is basically just busy-polling.
    #
    # Instead we just punt the whole thing into a thread.
    #
    # On a related note, it doesn't seem like there's any good way to nest asyncio -> tinyio -> asyncio.
    def run():
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        except BaseException:
            for task in asyncio.all_tasks(loop):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    loop.run_until_complete(task)
            loop.run_until_complete(loop.shutdown_asyncgens())
            raise

    return (yield run_in_thread(run))


async def to_asyncio(coro: Coro[_Return], exception_group: None | bool = None) -> Coroutine[Any, Any, _Return]:
    """Converts a `tinyio` coroutine into an `asyncio` coroutine.

    **Arguments:**

    - `coro`: a `tinyio` coroutine.
    - `exception_group`: as the `exception_group` parameter of [`tinyio.Loop.run`][].

    **Returns:**

    An `asyncio` coroutine.

    !!! Example

        ```python
        def add_one(x):
            yield tinyio.sleep(1)
            return x + 1

        async def foo(x):
            y = await tinyio.to_asyncio(add_one(x))
            return y

        asyncio.run(foo(3))
        ```
    """
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


def from_trio(coro: Awaitable[_Return]) -> Coro[_Return]:
    """Converts a `trio` coroutine into a `tinyio` coroutine.

    Uses trio's guest mode to run trio on top of the tinyio event loop.

    **Arguments:**

    - `coro`: a `trio` coroutine.

    **Returns:**

    A `tinyio` coroutine.

    !!! Example

        ```python
        async def add_one(x):
            await trio.sleep(1)
            return x + 1

        def foo(x):
            y = yield tinyio.from_trio(add_one(x))
            return y

        tinyio.Loop().run(foo(3))
        ```
    """
    import trio  # pyright: ignore[reportMissingImports]

    callback_queue: queue.Queue[Callable[[], Any]] = queue.Queue()
    event = Event()
    outcome_ref: list[Any] = [None]
    done_ref = [False]
    cancel_scope_ref: list[Any] = [None]

    # I think this should be threadsafe as-is without any extra threading locks etc. necessary.
    def run_sync_soon_threadsafe(fn: Callable[[], Any]) -> None:
        callback_queue.put(fn)
        event.set()

    def done_callback(outcome: Any) -> None:
        outcome_ref[0] = outcome
        done_ref[0] = True
        event.set()

    async def _wrapper():
        with trio.CancelScope() as cancel_scope:
            cancel_scope_ref[0] = cancel_scope
            return await coro

    trio.lowlevel.start_guest_run(
        _wrapper,
        run_sync_soon_threadsafe=run_sync_soon_threadsafe,
        done_callback=done_callback,
    )

    def _drain_callbacks() -> None:
        while True:
            try:
                fn = callback_queue.get_nowait()
            except queue.Empty:
                break
            fn()

    try:
        while not done_ref[0]:
            yield event.wait()
            event.clear()
            _drain_callbacks()
    except BaseException:
        # An external cancellation (e.g. tinyio.CancelledError) has been thrown into us.
        # Cancel the trio guest run and drain until it finishes.
        cancel_scope = cancel_scope_ref[0]
        if cancel_scope is not None:
            cancel_scope.cancel()
        # We still need to process remaining trio callbacks until the guest run is done.
        while not done_ref[0]:
            _drain_callbacks()
        raise

    # We're unwrapping an `outcome.Outcome` object.
    return cast(Any, outcome_ref[0]).unwrap()


async def to_trio(coro: Coro[_Return], exception_group: None | bool = None) -> _Return:
    """Converts a `tinyio` coroutine into a `trio` coroutine.

    **Arguments:**

    - `coro`: a `tinyio` coroutine.
    - `exception_group`: as the `exception_group` parameter of [`tinyio.Loop.run`][].

    **Returns:**

    A `trio` coroutine.

    !!! Example

        ```python
        def add_one(x):
            yield tinyio.sleep(1)
            return x + 1

        async def foo(x):
            y = await tinyio.to_trio(add_one(x))
            return y

        trio.run(foo, 3)
        ```
    """
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
