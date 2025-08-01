import contextlib
import re
import threading
import time

import pytest
import tinyio


def test_semaphore():
    counter = 0

    def _count(semaphore, i):
        nonlocal counter
        with (yield semaphore()):
            counter += 1
            if counter > 2:
                raise RuntimeError
            yield
            counter -= 1
        return i

    def _run(value):
        semaphore = tinyio.Semaphore(value)
        out = yield [_count(semaphore, i) for i in range(50)]
        return out

    loop = tinyio.Loop()
    assert loop.run(_run(2)) == list(range(50))
    with pytest.raises(RuntimeError):
        loop.run(_run(3))


def test_semaphore_reuse():
    counter = 0

    def _foo(coro):
        nonlocal counter
        context = yield coro
        counter += 1
        with pytest.raises(RuntimeError, match="do not") if counter == 2 else contextlib.nullcontext():
            with context:
                yield

    def _bar():
        semaphore = tinyio.Semaphore(2)
        coro = semaphore()
        yield [_foo(coro), _foo(coro)]

    loop = tinyio.Loop()
    loop.run(_bar())


def test_lock():
    counter = 0

    def _count(semaphore, i):
        nonlocal counter
        with (yield semaphore()):
            counter += 1
            if counter > 1:
                raise RuntimeError
            yield
            counter -= 1
        return i

    def _run():
        semaphore = tinyio.Lock()
        out = yield [_count(semaphore, i) for i in range(50)]
        return out

    loop = tinyio.Loop()
    assert loop.run(_run()) == list(range(50))


def test_barrier():
    barrier = tinyio.Barrier(3)
    count = 0

    def _foo():
        nonlocal count
        count += 1
        i = yield barrier.wait()
        time.sleep(0.1)
        assert count == 3
        return i

    def _run():
        out = yield [_foo() for _ in range(3)]
        return out

    loop = tinyio.Loop()
    assert set(loop.run(_run())) == {0, 1, 2}


def test_event():
    event = tinyio.Event()
    done = False
    done2 = False

    def _foo():
        nonlocal done
        assert event.is_set() is False
        yield event.wait()
        assert event.is_set() is True
        done = True

    def _bar():
        nonlocal done2
        yield {_foo()}
        for _ in range(10):
            yield
        assert not done
        assert event.is_set() is False
        event.set()
        assert event.is_set()
        done2 = True

    loop = tinyio.Loop()
    loop.run(_bar())
    assert done
    assert done2
    assert event.is_set()


@pytest.mark.parametrize("is_set", (False, True))
def test_event_only(is_set: bool):
    event = tinyio.Event()
    if is_set:
        event.set()

    def foo():
        if not is_set:
            t = threading.Timer(0.1, lambda: event.set())
            t.start()
        yield event.wait()

    loop = tinyio.Loop()
    loop.run(foo())


@pytest.mark.parametrize("is_set", (False, True))
def test_event_run(is_set: bool):
    event = tinyio.Event()
    if is_set:
        event.set()
    loop = tinyio.Loop()
    if not is_set:
        t = threading.Timer(0.1, lambda: event.set())
        t.start()
    loop.run(event.wait())


@pytest.mark.parametrize("is_set", (False, True))
def test_event_repeated_wait(is_set: bool):
    event = tinyio.Event()
    if is_set:
        event.set()

    def foo():
        wait = event.wait()
        if not is_set:
            t = threading.Timer(0.1, lambda: event.set())
            t.start()
        yield wait
        yield wait

    loop = tinyio.Loop()
    with pytest.raises(RuntimeError, match=re.escape("Do not yield the same `event.wait()` multiple times")):
        loop.run(foo())


@pytest.mark.parametrize("is_set", (False, True))
def test_event_simultaneous_wait(is_set: bool):
    event = tinyio.Event()
    if is_set:
        event.set()

    def _foo():
        if not is_set:
            t = threading.Timer(0.1, lambda: event.set())
            t.start()
        yield [event.wait(), event.wait()]

    loop = tinyio.Loop()
    loop.run(_foo())


@pytest.mark.parametrize("is_set", (False, True))
def test_event_simultaneous_repeated_wait(is_set: bool):
    event = tinyio.Event()
    if is_set:
        event.set()

    def foo():
        wait = event.wait()
        if not is_set:
            t = threading.Timer(0.1, lambda: event.set())
            t.start()
        yield [wait, wait]

    loop = tinyio.Loop()
    with pytest.raises(RuntimeError, match=re.escape("Do not yield the same `event.wait()` multiple times")):
        loop.run(foo())


def test_event_clear_not_strict():
    event = tinyio.Event()
    event.set()
    event.clear()
    assert not event.is_set()

    out = []

    def foo():
        yield event.wait()
        out.append(2)

    def bar():
        yield
        out.append(1)
        event.set()
        yield
        # Even though we `clear()` the event again afterwards, both `foo()` still unblock.
        event.clear()
        out.append(3)

    def baz():
        yield [foo(), foo(), bar()]

    loop = tinyio.Loop()
    loop.run(baz())
    assert out == [1, 2, 2, 3]


class _Semaphore:
    def __init__(self, value):
        self.value = value
        self.event = tinyio.Event()
        self.event.set()

    def __call__(self):
        while True:
            yield self.event.wait()
            if self.event.is_set():
                break
        assert self.value > 0
        self.value -= 1
        if self.value == 0:
            self.event.clear()
        return _closing(self)


@contextlib.contextmanager
def _closing(semaphore):
    try:
        yield
    finally:
        semaphore.value += 1
        semaphore.event.set()


def test_alternate_semaphore():
    """This test is useful as it makes use of `Event.clear()`."""

    counter = 0

    def _count(semaphore, i):
        nonlocal counter
        with (yield semaphore()):
            counter += 1
            if counter > 2:
                raise RuntimeError
            yield
            counter -= 1
        return i

    def _run(value):
        semaphore = _Semaphore(value)
        out = yield [_count(semaphore, i) for i in range(50)]
        return out

    loop = tinyio.Loop()
    assert loop.run(_run(2)) == list(range(50))
    with pytest.raises(RuntimeError):
        loop.run(_run(3))
