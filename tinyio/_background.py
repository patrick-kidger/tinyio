from collections.abc import Generator, Iterable
from typing import TypeVar

from ._core import Coro, Event


_T = TypeVar("_T")


def as_completed(coros: set[Coro[_T]]) -> Coro[Iterable[Coro[_T]]]:
    """Schedules multiple coroutines, iterating through their outputs in the order that they complete.

    Usage is as follows:
    ```python
    import tinyio

    def sleep(x):
        yield tinyio.sleep(x)
        return x

    def as_completed_demo():
        for out in (yield tinyio.as_completed({sleep(7), sleep(2), sleep(4)})):
            out = yield out
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
    return as_completed_iterator(outs, events)


# We'd much rather implement this as just
# ```python
# def as_completed_iterator(outs, events):
#     for i in range(len(events)):
#         yield as_completed_i(i, outs, events)
# ```
# but we want to have `.done` and `.get` attributes to offer a nice deprecation message.
class as_completed_iterator:
    def __init__(self, outs, events):
        self.outs = outs
        self.events = events

    def __iter__(self):
        for i in range(len(self.events)):
            yield as_completed_i(i, self.outs, self.events)

    def done(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(_deprecated_0_4_0_msg)

    def get(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(_deprecated_0_4_0_msg)


def as_completed_i(i, outs, events):
    yield events[i].wait()
    return outs[i]


_deprecated_0_4_0_msg = (
    "Calling `.get` or `.done` on `tinyio.as_completed` was deprecated in tinyio "
    "v0.4.0. Please use the new syntax:\n"
    "\n"
    "    for out in (yield tinyio.as_completed({...})):\n"
    "        out = yield out\n"
    "        ...\n"
)
