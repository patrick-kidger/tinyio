import contextlib
from typing import TypeVar

from ._core import Coro, Event


_T = TypeVar("_T")


def sleep(delay_in_seconds: int | float) -> Coro[None]:
    """`tinyio` coroutine for sleeping without blocking the event loop.

    **Arguments:**

    - `delay_in_seconds`: the number of seconds to sleep for.

    **Returns:**

    A coroutine that just sleeps.
    """
    yield from Event().wait(delay_in_seconds)


class TimeoutError(BaseException):
    """Raised by [`tinyio.TimeoutError`][] inside a coroutine that has not completed within its timeout."""


TimeoutError.__module__ = "tinyio"


def timeout(coro: Coro[_T], timeout_in_seconds: int | float) -> Coro[tuple[None | _T, bool]]:
    """`tinyio` coroutine for running a coroutine for at most `timeout_in_seconds`.

    **Arguments:**

    - `coro`: another coroutine.
    - `timeout_in_seconds`: the maximum number of seconds to allow `coro` to run for.

    **Returns:**

    A coroutine that an be `yield`ed on. This will return a pair of either `(output, True)` or `(None, False)`,
    corresponding to whether `coro` completed within the timeout or not.

    !!! warning

        In the event of a timeout, then note that whilst `coro` will have a `TimeoutError` arising from the `yield`
        point it is currently waiting at, it will *not* also cancel any other coroutines it has scheduled or is waiting
        on.
    """
    done = Event()
    outs = []

    def wrapper():
        out = yield coro
        outs.append(out)
        done.set()

    yield {wrapper()}
    yield from done.wait(timeout_in_seconds)
    if len(outs) == 0:
        with contextlib.suppress(TimeoutError):
            coro.throw(TimeoutError)
        return None, False
    else:
        [out] = outs
        return out, True
