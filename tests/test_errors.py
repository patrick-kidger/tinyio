import pickle
import time

import pytest
import tinyio


def _flat_tb(e: BaseException) -> list[str]:
    tb = e.__traceback__
    out = []
    while tb is not None:
        out.append(tb.tb_frame.f_code.co_name)
        tb = tb.tb_next
    return out


def test_propagation():
    def f():
        yield g()

    def g():
        yield from g2()

    def g2():
        yield h()

    def h():
        yield i()

    def i():
        raise RuntimeError("oh no")
        yield

    loop = tinyio.Loop()
    with pytest.raises(ExceptionGroup) as catcher:
        loop.run(f())
    [runtime] = catcher.value.exceptions
    assert type(runtime) is RuntimeError
    assert _flat_tb(runtime) == ["f", "g", "g2", "h", "i"]


def test_cancelling_coroutines_not_affecting_current_error():
    cancelled = False

    def f():
        yield [g(), h()]

    def g():
        yield i()

    def h():
        try:
            while True:
                yield tinyio.sleep(1)
        except BaseException as e:
            assert type(e) is tinyio.CancelledError
            nonlocal cancelled
            cancelled = True
            raise

    def i():
        raise RuntimeError("kapow")
        yield

    loop = tinyio.Loop()
    with pytest.raises(BaseExceptionGroup) as catcher:
        loop.run(f())
    assert cancelled
    [a, b, c] = catcher.value.exceptions
    assert type(a) is RuntimeError
    assert str(a) == "kapow"
    assert _flat_tb(a) == ["f", "g", "i"]
    assert _flat_tb(b) == ["h"]
    assert _flat_tb(c) == ["sleep"]


def test_invalid_yield():
    def f():
        yield g()

    def g():
        yield h(), h()

    def h():
        yield

    loop = tinyio.Loop()
    with pytest.raises(BaseExceptionGroup) as catcher:
        loop.run(f())
    [runtime] = catcher.value.exceptions
    assert type(runtime) is RuntimeError
    assert "Invalid yield" in str(runtime)
    assert _flat_tb(runtime) == ["f", "g"]


def _foo():
    yield _bar()


def _bar():
    yield _baz()


def _baz():
    raise RuntimeError("Kaboom")


@pytest.mark.parametrize("with_notes", (False, True))
def test_serialize(with_notes: bool):
    loop = tinyio.Loop()
    with pytest.raises(BaseExceptionGroup) as catcher:
        loop.run(_foo())
    if with_notes:
        catcher.value.add_note("hi")
        catcher.value.add_note("bye")
    data = pickle.dumps(catcher.value)
    out = pickle.loads(data)
    if with_notes:
        assert out.__notes__ == ["hi", "bye"]
    assert type(out) is ExceptionGroup
    [runtime] = out.exceptions
    assert type(runtime) is RuntimeError
    assert str(runtime) == "Kaboom"
    # Pickle strips tracebacks
    assert _flat_tb(runtime) == []


def test_error_to_thread():
    def blocking_operation():
        nonlocal marker
        assert marker is None

        # Do some blocking work.
        try:
            marker = False
            timeout = time.time() + 10
            while time.time() < timeout:
                time.sleep(0.1)
        except BaseException as e:
            assert type(e) is tinyio.CancelledError
            marker = True
            raise

    def cancel():
        yield from cancel2()

    def cancel2():
        while True:
            yield
            if marker is False:
                raise RuntimeError("Raising!")

    def one():
        yield [tinyio.run_in_thread(blocking_operation), cancel()]

    def two():
        yield [cancel(), tinyio.run_in_thread(blocking_operation)]

    loop = tinyio.Loop()

    for workflow in [one, two]:
        marker = None
        with pytest.raises(BaseExceptionGroup) as catcher:
            loop.run(workflow())
        assert marker is True
        [raising, cancelled] = catcher.value.exceptions
        assert type(raising) is RuntimeError and str(raising) == "Raising!"
        assert type(cancelled) is tinyio.CancelledError
        assert _flat_tb(raising) == [workflow.__name__, "cancel", "cancel2"]
        assert _flat_tb(cancelled) == ["target", "blocking_operation"]


def test_error_to_thread_with_context():
    def blocking_operation():
        try:
            sub_blocking_operation()
        except tinyio.CancelledError as e:
            raise ValueError("Responding improperly to cancellation") from e

    def sub_blocking_operation():
        while True:
            time.sleep(0.1)

    def foo():
        yield bar()

    def bar():
        yield [baz(), tinyio.run_in_thread(blocking_operation)]

    def baz():
        yield
        raise RuntimeError("Kaboom")

    loop = tinyio.Loop()
    with (
        pytest.raises(BaseExceptionGroup) as catcher,
        pytest.warns(RuntimeWarning, match="did not respond properly to cancellation"),
    ):
        loop.run(foo())
    runtime, value = catcher.value.exceptions
    assert type(runtime) is RuntimeError
    assert str(runtime) == "Kaboom"
    assert _flat_tb(runtime) == ["foo", "bar", "baz"]
    assert type(value) is ValueError
    assert str(value) == "Responding improperly to cancellation"
    assert _flat_tb(value) == ["target", "blocking_operation"]
    cancelled = value.__context__
    assert cancelled is value.__cause__
    assert type(cancelled) is tinyio.CancelledError
    assert _flat_tb(cancelled) == ["blocking_operation", "sub_blocking_operation"]


def test_error_from_thread():
    def blocking_operation():
        sub_blocking_operation()

    def sub_blocking_operation():
        raise RuntimeError("Kaboom")

    def foo():
        yield bar()

    def bar():
        yield [baz(), tinyio.run_in_thread(blocking_operation)]

    def baz():
        yield

    loop = tinyio.Loop()
    with pytest.raises(BaseExceptionGroup) as catcher:
        loop.run(foo())
    runtime, cancelled = catcher.value.exceptions
    assert type(runtime) is RuntimeError
    assert str(runtime) == "Kaboom"
    assert _flat_tb(runtime) == ["foo", "bar", "target", "blocking_operation", "sub_blocking_operation"]
    assert type(cancelled) is tinyio.CancelledError
    assert _flat_tb(cancelled) == ["baz"]


def test_error_from_thread_with_context():
    def blocking_operation():
        try:
            sub_blocking_operation()
        except RuntimeError as e:
            raise ValueError("oh no") from e

    def sub_blocking_operation():
        raise RuntimeError("Kaboom")

    def foo():
        yield bar()

    def bar():
        yield [baz(), tinyio.run_in_thread(blocking_operation)]

    def baz():
        yield

    loop = tinyio.Loop()
    with pytest.raises(BaseExceptionGroup) as catcher:
        loop.run(foo())
    value, cancelled = catcher.value.exceptions
    assert type(value) is ValueError
    assert str(value) == "oh no"
    assert _flat_tb(value) == ["foo", "bar", "target", "blocking_operation"]
    assert type(cancelled) is tinyio.CancelledError
    assert _flat_tb(cancelled) == ["baz"]

    runtime = value.__context__
    assert type(runtime) is RuntimeError
    assert str(runtime) == "Kaboom"
    assert _flat_tb(runtime) == ["blocking_operation", "sub_blocking_operation"]


def test_keyboard_interrupt_within_loop(monkeypatch):
    """Tests an error occurring from within the loop itself, not within one of the coroutines."""

    def _invalid(out):
        del out
        raise KeyboardInterrupt

    monkeypatch.setattr(tinyio._core, "_invalid", _invalid)

    def _f():
        yield [_g(), _h()]

    def _g():
        yield 5

    def _h():
        yield

    loop = tinyio.Loop()
    with pytest.raises(BaseExceptionGroup) as catcher:
        loop.run(_f())
    [keyboard, h_error] = catcher.value.exceptions
    assert type(keyboard) is KeyboardInterrupt
    assert _flat_tb(keyboard) == ["_f", "_g", "_invalid"]
    assert _flat_tb(h_error) == ["_h"]
