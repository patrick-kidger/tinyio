import collections as co
import contextlib

from ._core import Coro, Event


class Semaphore:
    """Limits coroutines so that at most `value` of them can access a resource concurrently.

    Usage:
    ```python
    semaphore = tinyio.Semaphore(value=...)

    with (yield semaphore()):
        ...
    ```
    """

    def __init__(self, value: int):
        """**Arguments:**

        - `value`: the maximum number of concurrent accesses.
        """
        if value <= 0:
            raise ValueError("`tinyio.Semaphore(value=...)` must be positive.")
        self._value = value
        self._events = co.deque[Event]()

    def __call__(self) -> Coro[contextlib.AbstractContextManager[None]]:
        if self._value == 0:
            event = Event()
            self._events.append(event)
            yield from event.wait()
        assert self._value > 0
        self._value -= 1
        return _CloseSemaphore(self, [False])


class _CloseSemaphore:
    def __init__(self, semaphore: Semaphore, cell: list[bool]):
        self._semaphore = semaphore
        self._cell = cell

    def __enter__(self):
        if self._cell[0]:
            raise RuntimeError("Use a new `semaphore()` call in each `with (yield semaphore())`, do not re-use it.")
        self._cell[0] = True

    def __exit__(self, exc_type, exc_value, exc_tb):
        del exc_type, exc_value, exc_tb
        self._semaphore._value += 1
        if len(self._semaphore._events) > 0:
            event = self._semaphore._events.popleft()
            event.set()


class Lock:
    """Prevents multiple coroutines from accessing a single resource."""

    def __init__(self):
        self._semaphore = Semaphore(value=1)

    def __call__(self) -> Coro[contextlib.AbstractContextManager[None]]:
        return self._semaphore()


class Barrier:
    """Prevents coroutines from progressing until at least `value` of them have called `yield barrier.wait()`."""

    def __init__(self, value: int):
        self._count = 0
        self._value = value
        self._event = Event()

    def wait(self):
        count = self._count
        self._count += 1
        if self._count == self._value:
            self._event.set()
        yield from self._event.wait()
        return count
