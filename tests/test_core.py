import gc
import threading
import time
from typing import Any

import pytest
import tinyio


def _add_one(x: int) -> tinyio.Coro[int]:
    yield
    return x + 1


def _add_two(x: int) -> tinyio.Coro[int]:
    y = yield _add_one(x)
    z = yield _add_one(y)
    return z


def test_basic():
    loop = tinyio.Loop()
    assert loop.run(_add_two(4)) == 6


def test_gather():
    def _gather(x: int):
        return (yield [_add_one(x), _add_two(x)])

    loop = tinyio.Loop()
    assert loop.run(_gather(3)) == [4, 5]


def test_empty_gather():
    def _gather():
        out = yield []
        return out

    loop = tinyio.Loop()
    assert loop.run(_gather()) == []


def test_multi_yield():
    def _multi_yield():
        foo = _add_one(x=3)
        x = yield foo
        y = yield foo
        return x, y

    loop = tinyio.Loop()
    assert loop.run(_multi_yield()) == (4, 4)


def test_simultaneous_yield():
    def _simultaneous_yield():
        foo = _add_one(x=3)
        x, y = yield [foo, foo]
        return x, y

    loop = tinyio.Loop()
    assert loop.run(_simultaneous_yield()) == (4, 4)


def test_diamond():
    def _diamond1(x: int) -> tinyio.Coro[int]:
        y = _add_one(x)
        a, b = yield [_diamond2(y, 1), _diamond2(y, 2)]
        return a + b

    def _diamond2(y: tinyio.Coro[int], factor: int):
        z = yield y
        return z * factor

    loop = tinyio.Loop()
    assert loop.run(_diamond1(2)) == 9


def test_sleep():
    def _slow_add_one(x: int):
        yield tinyio.sleep(0.1)
        return x + 1

    def _big_gather(x: int):
        out = yield [_slow_add_one(x) for _ in range(100)]
        return out

    loop = tinyio.Loop()
    start = time.time()
    out = loop.run(_big_gather(1))
    end = time.time()
    assert out == [2 for _ in range(100)]
    assert end - start < 0.5


def test_multi_run():
    foo = _add_one(x=4)

    def _mul():
        out = yield foo
        return out * 5

    loop = tinyio.Loop()
    assert loop.run(_mul()) == 25
    assert loop.run(_mul()) == 25
    assert loop.run(_mul()) == 25


def test_waiting_on_already_finished():
    def f():
        yield
        return 3

    def g(coro):
        yield h(coro)
        yield [f(), coro]

    def h(coro):
        yield coro

    foo = f()
    loop = tinyio.Loop()
    loop.run(g(foo))


def test_cycle():
    def f():
        yield gg

    def g():
        yield ff

    ff = f()
    gg = g()

    def h():
        yield [ff, gg]

    loop = tinyio.Loop()
    with pytest.raises(RuntimeError, match="Cycle detected in `tinyio` loop"):
        loop.run(h(), exception_group=False)


@pytest.mark.parametrize("wait_on_f", (False, True))
def test_background(wait_on_f: bool):
    val = False
    done = False

    def f():
        while val is False:
            yield
        return 3

    def g():
        nonlocal val
        val = True
        yield

    def h():
        nonlocal done
        ff = f()
        out = yield {ff}
        assert out is None
        yield g()
        if wait_on_f:
            out = yield ff
            assert out == 3
        done = True

    loop = tinyio.Loop()
    loop.run(h())
    assert done


@pytest.mark.parametrize("wait_on_f", (False, True))
def test_background_already_waiting(wait_on_f: bool):
    val = False
    done = False

    def f():
        while val is False:
            yield
        return 3

    ff = f()

    def g():
        nonlocal val
        val = True
        yield

    def h():
        nonlocal done
        out = yield {ff}
        assert out is None
        yield g()
        if wait_on_f:
            out = yield ff
            assert out == 3
        done = True

    def i():
        yield [ff, h()]

    loop = tinyio.Loop()
    loop.run(i())
    assert done


def test_empty_background():
    def _background():
        yield set()
        return 3

    loop = tinyio.Loop()
    assert loop.run(_background()) == 3


def test_background_multiple_yields():
    done = False

    def f():
        yield
        return 3

    def g():
        nonlocal done
        ff = f()
        yield {ff}
        yield {ff}
        x = yield ff
        y = yield ff
        assert x == 3
        assert y == 3
        done = True

    loop = tinyio.Loop()
    loop.run(g())
    assert done


def test_no_yield_direct():
    def f():
        return 3
        yield

    loop = tinyio.Loop()
    assert loop.run(f()) == 3


def test_no_yield_indirect():
    def f():
        return 3
        yield

    def g():
        out = yield f()
        return out

    loop = tinyio.Loop()
    assert loop.run(g()) == 3


def test_gc():
    def _block_add_one(x):
        return x + 1

    def _foo(x):
        return (yield tinyio.run_in_thread(_block_add_one, x))

    def _gc(x: int) -> tinyio.Coro[tuple[int, int]]:
        iterator = tinyio.AsCompleted({_foo(x), _add_one(x)})
        y = yield iterator.get()
        z = yield iterator.get()
        return y, z

    loop = tinyio.Loop()
    coro = _gc(4)
    assert loop.run(coro) == (5, 5)
    gc.collect()
    assert set(loop._results.keys()) == {coro}


def test_event_fairness():
    """This checks that once one event unblocks, that we don't just keep chasing all the stuff downstream of that event,
    i.e. that we do schedule work from any other event that has finished.
    """
    outs = []

    def f():
        yield tinyio.Event().wait(0)
        outs.append(1)
        for _ in range(20):
            yield
        outs.append(2)

    def g():
        yield [f(), f()]

    loop = tinyio.Loop()
    loop.run(g())
    assert outs == [1, 1, 2, 2]


def test_event_fairness2():
    event1 = tinyio.Event()
    outs = []

    def f():
        yield event1.wait(0)
        outs.append(1)

    def g():
        yield {f()}
        for _ in range(20):
            yield
        outs.append(2)

    loop = tinyio.Loop()
    loop.run(g())
    assert outs == [1, 2]


def test_simultaneous_set():
    event = tinyio.Event()

    def f():
        for _ in range(20):
            yield
        yield [tinyio.run_in_thread(event.set) for _ in range(100)]

    def g():
        yield event.wait()

    def h():
        yield [g(), f()]

    loop = tinyio.Loop()
    loop.run(h())


def test_timeout_then_set():
    event1 = tinyio.Event()
    event2 = tinyio.Event()

    def f():
        yield [event1.wait(0), event2.wait()]

    def g():
        yield {f()}
        for _ in range(20):
            yield
        event1.set()
        for _ in range(20):
            yield
        event2.set()
        return 3

    loop = tinyio.Loop()
    assert loop.run(g()) == 3


def test_set_then_timeout():
    event1 = tinyio.Event()
    event2 = tinyio.Event()

    def f():
        event1.set()
        yield [event1.wait(0), event2.wait()]

    def g():
        yield {f()}
        for _ in range(20):
            yield
        event2.set()
        return 3

    loop = tinyio.Loop()
    assert loop.run(g()) == 3


def test_set_then_timeout_then_clear():
    event1 = tinyio.Event()
    event2 = tinyio.Event()

    def f():
        event1.set()
        yield [event1.wait(0), event2.wait()]

    def g():
        yield {f()}
        for _ in range(20):
            yield
        event1.clear()
        event2.set()
        return 3

    loop = tinyio.Loop()
    assert loop.run(g()) == 3


def test_set_then_timeout_then_clear_then_set():
    event1 = tinyio.Event()
    event2 = tinyio.Event()

    def f():
        event1.set()
        yield [event1.wait(0), event2.wait()]

    def g():
        yield {f()}
        for _ in range(20):
            yield
        event1.clear()
        event2.set()
        event1.set()
        return 3

    loop = tinyio.Loop()
    assert loop.run(g()) == 3


def test_timeout_as_part_of_group_and_only_coroutine():
    event1 = tinyio.Event()
    event2 = tinyio.Event()
    wait: Any = event1.wait(0)
    wait2 = event2.wait()

    def f():
        yield [wait, wait2]
        return 3

    def set2():
        time.sleep(0.1)
        event2.set()

    t = threading.Thread(target=set2)
    t.start()
    loop = tinyio.Loop()
    assert loop.run(f()) == 3
