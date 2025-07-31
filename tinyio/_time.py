import contextlib
import threading
import time
from typing import TypeVar

from ._background import add_done_callback
from ._core import Coro
from ._sync import Event
from ._thread import run_in_thread


_T = TypeVar("_T")


def sleep(delay_in_seconds: int | float) -> Coro[None]:
    """`tinyio` coroutine for sleeping without blocking the event loop.

    **Arguments:**

    - `delay_in_seconds`: the number of seconds to sleep for.

    **Returns:**

    A coroutine that just sleeps.
    """
    yield run_in_thread(time.sleep, delay_in_seconds)


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
    success = Event()

    def timeout():
        time.sleep(timeout_in_seconds)
        done.set()

    def callback(_):
        done.set()
        success.set()

    t = threading.Thread(target=timeout, daemon=True)
    t.start()
    yield {add_done_callback(coro, callback)}
    yield done.wait()
    if success.is_set():
        return (yield coro), True
    else:
        time.sleep(0.1)
        with contextlib.suppress(TimeoutError):
            coro.throw(TimeoutError)
        return None, False
