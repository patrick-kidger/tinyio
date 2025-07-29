import contextlib
import time
from typing import TypeVar

from ._background import add_done_callback
from ._core import Coro
from ._sync import Event


_T = TypeVar("_T")


def sleep(delay_in_seconds: int | float) -> Coro[None]:
    """`tinyio` coroutine for sleeping without blocking the event loop.

    **Arguments:**

    - `delay_in_seconds`: the number of seconds to sleep for.

    **Returns:**

    A coroutine that just sleeps.
    """
    timeout = time.monotonic() + delay_in_seconds
    while time.monotonic() <= timeout:
        yield


class TimeoutError(BaseException):
    pass


TimeoutError.__module__ = "tinyio"


def timeout(coro: Coro[_T], timeout_in_seconds: int | float) -> Coro[tuple[None | _T, bool]]:
    """`tinyio` coroutine for running a coroutine for at most `timeout_in_seconds`.

    **Arguments:**

    - `coro`: another coroutine.
    - `timeout_in_seconds`: the maximum number of seconds to allow `coro` to run for.

    **Returns:**

    A coroutine that an be `yield`ed on. This will return a pair of either `(output, True)` or `(None, False)`,
    corresponding to whether `coro` completed within the timeout or not.
    """
    done = Event()
    timeout = time.monotonic() + timeout_in_seconds
    yield {add_done_callback(coro, lambda _: done.set())}
    while time.monotonic() <= timeout and not done.is_set():
        yield
    if done.is_set():
        return (yield coro), True
    else:
        with contextlib.suppress(TimeoutError):
            coro.throw(TimeoutError)
        return None, False
