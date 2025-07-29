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
