import time

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


def _multi_yield():
    foo = _add_one(x=3)
    x = yield foo
    y = yield foo
    return x, y


def test_multi_yield():
    loop = tinyio.Loop()
    assert loop.run(_multi_yield()) == (4, 4)


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


def test_in_thread():
    def _blocking_slow_add_one(x: int) -> int:
        time.sleep(0.1)
        return x + 1

    def _big_gather(x: int):
        out = yield [tinyio.run_in_thread(_blocking_slow_add_one, x) for _ in range(100)]
        return out

    loop = tinyio.Loop()
    start = time.time()
    out = loop.run(_big_gather(1))
    end = time.time()
    assert out == [2 for _ in range(100)]
    assert end - start < 0.5


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
