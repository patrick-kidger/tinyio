import pytest
import tinyio


class SingleElementQueue:
    def __init__(self):
        self._event = tinyio.Event()
        self._elem = None

    def put(self, x):
        if self._elem is not None:
            raise ValueError("Queue is full")

        self._elem = x
        self._event.set()

    def get(self):
        while self._elem is None:
            yield self._event.wait()
        x = self._elem
        self._elem = None
        return x


@pytest.mark.parametrize("nest_g", (False, True))
@pytest.mark.parametrize("nest_h", (False, True))
def test_nest(nest_g: bool, nest_h: bool):
    """Test that all coroutines make progress when some are nested"""
    q1 = SingleElementQueue()
    q2 = SingleElementQueue()

    # Intertwine two coroutines in such a way that they can only
    # finish if both of them make progress at the same time, but
    # not if one blocks until the other has completed.
    def g() -> tinyio.Coro[int]:
        q1.put(1)
        x = yield q2.get()
        q1.put(x + 1)
        return (yield q2.get())

    def h() -> tinyio.Coro[int]:
        x = yield q1.get()
        q2.put(x + 1)
        x = yield q1.get()
        q2.put(x + 1)
        return x

    def maybe_nest(c: tinyio.Coro[int], nest: bool) -> tinyio.Coro[int]:
        if nest:
            return tinyio.nest(c)
        else:
            return c

    def f() -> tinyio.Coro[list[int]]:
        return (yield [maybe_nest(g(), nest_g), maybe_nest(h(), nest_h)])

    out = tinyio.Loop().run(f())
    assert out == [4, 3]


def test_nest_with_error_in_inner_loop():
    """Test that if an inner coroutine raises an exception, nested
    coroutines are cancelled but outer ones keep running"""
    q1 = SingleElementQueue()
    q2 = SingleElementQueue()

    g_was_cancelled = True
    i_was_cancelled = True

    def g() -> tinyio.Coro[int]:
        nonlocal g_was_cancelled
        q2.put(5)
        yield tinyio.sleep(1)
        g_was_cancelled = False
        return 0

    def h() -> tinyio.Coro[int]:
        x = yield q1.get()
        y = yield q2.get()
        if x == 5 and y == 5:
            raise RuntimeError("Kaboom")
        return x + y

    def i() -> tinyio.Coro[int]:
        nonlocal i_was_cancelled
        q1.put(5)
        yield tinyio.sleep(1)
        i_was_cancelled = False
        return 0

    def nested() -> tinyio.Coro[list[int]]:
        return (yield [h(), i()])

    def f() -> tinyio.Coro[list[int | list[int]]]:
        return (yield [g(), tinyio.nest(nested())])

    try:
        tinyio.Loop().run(f())
    except RuntimeError:
        pass

    assert not g_was_cancelled
    assert i_was_cancelled
