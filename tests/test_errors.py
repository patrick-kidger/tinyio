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


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_propagation(exception_group):
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
    with pytest.raises(Exception) as catcher:
        loop.run(f(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is ExceptionGroup
        [runtime] = catcher.value.exceptions  # pyright: ignore[reportAttributeAccessIssue]
        assert type(runtime) is RuntimeError
        assert _flat_tb(runtime) == ["f", "g", "g2", "h", "i"]
    else:
        runtime = catcher.value
        assert type(runtime) is RuntimeError
        assert _flat_tb(runtime) == ["test_propagation", "f", "g", "g2", "h", "i"]


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_cancelling_coroutines_not_affecting_current_error(exception_group):
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
    with pytest.raises(BaseException) as catcher:
        loop.run(f(), exception_group)
    assert cancelled
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        [a, b, c] = catcher.value.exceptions
        assert type(a) is RuntimeError
        assert str(a) == "kapow"
        assert _flat_tb(a) == ["f", "g", "i"]
        assert _flat_tb(b) == ["h"]
        assert _flat_tb(c) == ["sleep"]
    else:
        assert type(catcher.value) is RuntimeError
        assert str(catcher.value) == "kapow"
        assert _flat_tb(catcher.value) == ["test_cancelling_coroutines_not_affecting_current_error", "f", "g", "i"]


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_invalid_yield(exception_group):
    def f():
        yield g()

    def g():
        yield h(), h()

    def h():
        yield

    loop = tinyio.Loop()
    with pytest.raises(Exception) as catcher:
        loop.run(f(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is ExceptionGroup
        [runtime] = catcher.value.exceptions
        assert _flat_tb(runtime) == ["f", "g"]
    else:
        runtime = catcher.value
        assert _flat_tb(runtime) == ["test_invalid_yield", "f", "g"]
    assert type(runtime) is RuntimeError
    assert "Invalid yield" in str(runtime)


def _foo():
    yield _bar()


def _bar():
    yield _baz()


def _baz():
    raise RuntimeError("Kaboom")


@pytest.mark.parametrize("exception_group", (None, False, True))
@pytest.mark.parametrize("with_notes", (False, True))
def test_serialize(exception_group: bool, with_notes: bool):
    loop = tinyio.Loop()
    with pytest.raises(BaseException) as catcher:
        loop.run(_foo(), exception_group)
    if with_notes:
        catcher.value.add_note("hi")
        catcher.value.add_note("bye")
    data = pickle.dumps(catcher.value)
    out = pickle.loads(data)
    if with_notes:
        assert out.__notes__ == ["hi", "bye"]
    if exception_group is True:
        assert type(out) is ExceptionGroup
        [runtime] = out.exceptions
    else:
        runtime = out
    assert type(runtime) is RuntimeError
    assert str(runtime) == "Kaboom"
    # Pickle strips tracebacks
    assert _flat_tb(runtime) == []


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_error_to_thread(exception_group):
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
        with pytest.raises(BaseException) as catcher:
            loop.run(workflow(), exception_group)
        assert marker is True
        if exception_group is True:
            assert type(catcher.value) is BaseExceptionGroup
            [raising, cancelled] = catcher.value.exceptions
            assert type(raising) is RuntimeError and str(raising) == "Raising!"
            assert type(cancelled) is tinyio.CancelledError
            assert _flat_tb(raising) == [workflow.__name__, "cancel", "cancel2"]
            assert _flat_tb(cancelled) == ["target", "blocking_operation"]
        else:
            raising = catcher.value
            assert type(raising) is RuntimeError and str(raising) == "Raising!"
            assert _flat_tb(raising) == ["test_error_to_thread", workflow.__name__, "cancel", "cancel2"]


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_error_to_thread_with_context(exception_group):
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
        pytest.raises(BaseException) as catcher,
        pytest.warns(RuntimeWarning, match="did not respond properly to cancellation"),
    ):
        loop.run(foo(), exception_group)
    if exception_group is False:
        runtime = catcher.value
        assert type(runtime) is RuntimeError
        assert str(runtime) == "Kaboom"
        assert _flat_tb(runtime) == ["test_error_to_thread_with_context", "foo", "bar", "baz"]
    else:
        assert type(catcher.value) is ExceptionGroup
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


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_error_from_thread(exception_group):
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
    with pytest.raises(BaseException) as catcher:
        loop.run(foo(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        runtime, cancelled = catcher.value.exceptions
        assert type(runtime) is RuntimeError
        assert str(runtime) == "Kaboom"
        assert _flat_tb(runtime) == ["foo", "bar", "target", "blocking_operation", "sub_blocking_operation"]
        assert type(cancelled) is tinyio.CancelledError
        assert _flat_tb(cancelled) == ["baz"]
    else:
        runtime = catcher.value
        assert type(runtime) is RuntimeError
        assert str(runtime) == "Kaboom"
        assert _flat_tb(runtime) == [
            "test_error_from_thread",
            "foo",
            "bar",
            "target",
            "blocking_operation",
            "sub_blocking_operation",
        ]


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_error_from_thread_with_context(exception_group):
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
    with pytest.raises(BaseException) as catcher:
        loop.run(foo(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        value, cancelled = catcher.value.exceptions
        assert type(value) is ValueError
        assert str(value) == "oh no"
        assert _flat_tb(value) == ["foo", "bar", "target", "blocking_operation"]
        assert type(cancelled) is tinyio.CancelledError
        assert _flat_tb(cancelled) == ["baz"]
    else:
        value = catcher.value
        assert type(value) is ValueError
        assert str(value) == "oh no"
        assert _flat_tb(value) == ["test_error_from_thread_with_context", "foo", "bar", "target", "blocking_operation"]

    runtime = value.__context__
    assert type(runtime) is RuntimeError
    assert str(runtime) == "Kaboom"
    assert _flat_tb(runtime) == ["blocking_operation", "sub_blocking_operation"]


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_keyboard_interrupt_within_loop(exception_group, monkeypatch):
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
    with pytest.raises(BaseException) as catcher:
        loop.run(_f(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        [keyboard, h_error] = catcher.value.exceptions
        assert type(keyboard) is KeyboardInterrupt
        assert _flat_tb(keyboard) == ["_f", "_g", "_invalid"]
        assert _flat_tb(h_error) == ["_h"]
    else:
        keyboard = catcher.value
        assert type(keyboard) is KeyboardInterrupt
        assert _flat_tb(keyboard) == ["test_keyboard_interrupt_within_loop", "_f", "_g", "_invalid"]
