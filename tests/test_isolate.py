from collections.abc import Callable

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


@pytest.mark.parametrize("isolate_g", (False, True))
@pytest.mark.parametrize("isolate_h", (False, True))
def test_isolate(isolate_g: bool, isolate_h: bool):
    """Test that all coroutines make progress when some are isolated"""
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

    def maybe_isolate(c: Callable[[], tinyio.Coro[int]], isolate: bool) -> tinyio.Coro[int]:
        if isolate:
            x, _ = yield tinyio.isolate(c)
            return x
        else:
            return (yield c())

    def f() -> tinyio.Coro[list[int]]:
        return (yield [maybe_isolate(g, isolate_g), maybe_isolate(h, isolate_h)])

    out = tinyio.Loop().run(f())
    assert out == [4, 3]


def test_isolate_with_error_in_inner_loop():
    """Test exceptions happening in the isolated loop.

    If an isolated coroutine raises an exception, all other coroutines within
    the isolation are cancelled, but outer coroutines keep running."""
    q1 = SingleElementQueue()
    q2 = SingleElementQueue()
    q3 = SingleElementQueue()

    g_was_cancelled = True
    i_was_cancelled = True

    def g() -> tinyio.Coro[int]:
        nonlocal g_was_cancelled
        q2.put(5)
        yield q3.get()
        g_was_cancelled = False
        return 1

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
        return 2

    def isolated() -> tinyio.Coro[list[int]]:
        return (yield [h(), i()])

    def try_isolated() -> tinyio.Coro[list[int]]:
        x, success = yield tinyio.isolate(isolated)
        if not success:
            x = [-1, -1]

        q3.put(0)  # wake up the "outer" loop g()
        return x

    def f() -> tinyio.Coro[list[int]]:
        return (yield [g(), try_isolated()])

    assert tinyio.Loop().run(f()) == [1, [-1, -1]]

    assert not g_was_cancelled
    assert i_was_cancelled


def test_isolate_with_args():
    """Test that isolate can be called with additional coroutines as arguments"""

    def slow_add_one(x: int) -> tinyio.Coro[int]:
        yield
        return x + 1

    def unreliable_add_two(get_x: tinyio.Coro[int]) -> tinyio.Coro[int]:
        x = yield get_x
        if x == 3:
            raise RuntimeError("That is too hard.")
        else:
            y = yield slow_add_one(x)
            z = yield slow_add_one(y)
            return z

    def try_add_three(x: int) -> tinyio.Coro[tuple[int, bool]]:
        return (yield tinyio.isolate(unreliable_add_two, slow_add_one(x)))

    assert tinyio.Loop().run(try_add_three(0)) == (3, True)
    assert tinyio.Loop().run(try_add_three(1)) == (4, True)
    assert tinyio.Loop().run(try_add_three(3)) == (6, True)
    assert tinyio.Loop().run(try_add_three(4)) == (7, True)

    result, success = tinyio.Loop().run(try_add_three(2))
    assert not success
    assert type(result) is RuntimeError
    assert str(result) == "That is too hard."
