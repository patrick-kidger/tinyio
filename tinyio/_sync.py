import contextlib

from ._core import Coro


class Event:
    """A marker than something has happened."""

    def __init__(self):
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False

    def wait(self):
        while not self._set:
            yield


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
        self._event = Event()
        self._event.set()

    def __call__(self) -> Coro[contextlib.AbstractContextManager[None]]:
        while True:
            yield self._event.wait()
            if self._event.is_set():
                break
        assert self._value > 0
        self._value -= 1
        if self._value == 0:
            self._event.clear()
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
        self._semaphore._event.set()


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
        yield self._event.wait()
        return count
