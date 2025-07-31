from collections.abc import Callable, Generator
from typing import Generic, TypeVar

from ._core import Coro, Event


_T = TypeVar("_T")


# We don't allow an error callback for mildly complicated reasons:
# In order to get the exception object in this coroutine (rather than just a `CancelledError`) then we would need to
# `yield from coro`. However that means the main loop would also not see `coro` and thus not be able to record its
# output. Any other `yield coro` calls (which may occur as we do not control where else `coro` is used) would just
# give `None`.
# We could find a way to finesse this (stick with just `yield` and attach the coroutine information to the cancelled
# error, so we can identify that this is 'our' error and that we should call our error callback) but at least right now
# we don't have a use-case.
def add_done_callback(coro: Coro[_T], success_callback: Callable[[_T], None]) -> Coro[_T]:
    """Wraps `coro` so that `success_callback` is called once it completes.

    This function is typically used as `yield {add_done_callback(...)}`.  Note the `{...}`, indicating that calling this
    in nonblocking fashion (otherwise you could just directly call the callback yourself).

    This allows for scheduling work in the background, along with behaviour once it completes.

    **Arguments:**

    - `coro`: the coroutine to wrap.
    - `success_callback`: if `coro` completes successfully then this will be called with a single argument, which is the
        output of `coro`.

    **Returns:**

    A coroutine that returns nothing.

    !!! Example

        ```python
        import tinyio

        def sleep(x):
            yield tinyio.sleep(x)
            return x

        def done_callback_demo():
            success_callback = lambda out: print(f"Done callback demo: {out}")
            yield {tinyio.add_done_callback(sleep(2), success_callback)}
            yield {tinyio.add_done_callback(sleep(1), success_callback)}
            print("Hello")

        loop = tinyio.Loop()
        loop.run(done_callback_demo())
        # Hello
        # Done callback demo: 1
        # Done callback demo: 2
        ```
    """
    out = yield coro
    success_callback(out)
    return out


class AsCompleted(Generic[_T]):
    """Schedules multiple coroutines, iterating through their outputs in the order that they complete.

    !!! Example

        ```python
        import tinyio

        def sleep(x):
            yield tinyio.sleep(x)
            return x

        def as_completed_demo():
            iterator = tinyio.AsCompleted({sleep(7), sleep(2), sleep(4)})
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

    def __init__(self, coros: set[Coro[_T]]):
        if not isinstance(coros, set) or any(not isinstance(coro, Generator) for coro in coros):
            raise ValueError("`AsCompleted(coros=...)` must be a set of coroutines.")
        self._coros = set(coros)
        self._put_count = 0
        self._get_count = 0
        self._outs = {}
        self._events = [Event() for _ in self._coros]
        self._started = False

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
        if not self._started:
            self._started = True

            def callback(out):
                self._outs[self._put_count] = out
                self._events[self._put_count].set()
                self._put_count += 1

            yield {add_done_callback(coro, callback) for coro in self._coros}
            self._coros.clear()  # Enable them to be GC'd as they complete.
        yield self._events[get_count].wait()
        return self._outs.pop(get_count)
