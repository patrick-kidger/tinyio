import contextlib

from ._core import Coro


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

    def __call__(self) -> Coro[contextlib.AbstractContextManager[None]]:
        while self._value <= 0:
            assert self._value >= 0
            yield
        self._value -= 1
        return _close_semaphore(self, [False])


@contextlib.contextmanager
def _close_semaphore(semaphore: Semaphore, cell: list[bool]):
    if cell[0]:
        raise RuntimeError("Use a new `semaphore()` call in each `with (yield semaphore())`, do not re-use it.")
    cell[0] = True
    try:
        yield
    finally:
        semaphore._value += 1


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

    def wait(self):
        count = self._count
        self._count += 1
        while self._count < self._value:
            yield
        return count


class Event:
    """A marker than something has happened."""

    def __init__(self):
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self):
        while not self._set:
            yield
