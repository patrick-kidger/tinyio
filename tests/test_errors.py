import contextlib
import os
import pickle
import signal
import threading
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


def _make_cycle():
    def f():
        yield gg

    def g():
        yield ff

    ff = f()
    gg = g()

    def h():
        yield [ff, gg]

    return h()


def test_cycle():
    h = _make_cycle()
    loop = tinyio.Loop()
    with pytest.raises(tinyio.CancelledError, match="Cycle detected in `tinyio` loop") as catcher:
        loop.run(h, exception_group=False)
    assert _flat_tb(catcher.value) == ["test_cycle", "g", "h"]


def test_cycle_misbehaving_coroutine_yield():
    def wrapper():
        try:
            yield _make_cycle()
        except tinyio.CancelledError:
            yield 5

    loop = tinyio.Loop()
    with pytest.warns(match="Instead, the coroutine yielded `5`"):
        with pytest.raises(tinyio.CancelledError, match="Cycle detected in `tinyio` loop") as catcher:
            loop.run(wrapper(), exception_group=False)
    # Unlike the `test_cycle` case, we get one fewer frame in the traceback. I think this is because the place at which
    # we `coro.throw` the error responds improperly... so we don't get its frame to use in the traceback.
    assert _flat_tb(catcher.value) == ["test_cycle_misbehaving_coroutine_yield", "g"]


def test_cycle_misbehaving_coroutine_return():
    def wrapper():
        try:
            yield _make_cycle()
        except tinyio.CancelledError:
            return 5

    loop = tinyio.Loop()
    with pytest.warns(match="Instead, the coroutine returned `5`"):
        with pytest.raises(tinyio.CancelledError, match="Cycle detected in `tinyio` loop") as catcher:
            loop.run(wrapper(), exception_group=False)
    assert _flat_tb(catcher.value) == ["test_cycle_misbehaving_coroutine_return", "g"]


def test_cycle_misbehaving_coroutine_exception():
    def wrapper():
        try:
            yield _make_cycle()
        except tinyio.CancelledError:
            raise RuntimeError("kaboom")

    loop = tinyio.Loop()
    with pytest.warns(match="Instead, the coroutine raised the exception `RuntimeError: kaboom`"):
        with pytest.raises(tinyio.CancelledError, match="Cycle detected in `tinyio` loop") as catcher:
            loop.run(wrapper(), exception_group=False)
    assert _flat_tb(catcher.value) == ["test_cycle_misbehaving_coroutine_exception", "g"]


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
        assert _flat_tb(catcher.value) == ["test_propagation"]
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
def test_catch_cancelled_raise_new_cancelled(exception_group):
    ready = tinyio.Event()

    def f():
        yield [g(), h()]

    def g():
        yield i()

    def h():
        try:
            ready.set()
            while True:
                yield tinyio.sleep(1)
        except tinyio.CancelledError:
            raise tinyio.CancelledError("new cancellation")

    def i():
        yield ready.wait()
        raise RuntimeError("kapow")

    loop = tinyio.Loop()
    with pytest.raises(BaseException) as catcher:
        loop.run(f(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        [a, b, c] = catcher.value.exceptions
        assert type(a) is RuntimeError
        assert str(a) == "kapow"
        assert _flat_tb(a) == ["f", "g", "i"]
        assert type(b) is tinyio.CancelledError
        assert type(c) is tinyio.CancelledError
        assert {tuple(_flat_tb(b)), tuple(_flat_tb(c))} == {("h",), ("sleep", "wait")}
    else:
        assert type(catcher.value) is RuntimeError
        assert str(catcher.value) == "kapow"
        assert _flat_tb(catcher.value) == ["test_catch_cancelled_raise_new_cancelled", "f", "g", "i"]


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_invalid_yield(exception_group):
    def f():
        yield g()

    def g():
        yield h(), h()

    def h():
        yield

    loop = tinyio.Loop()
    with pytest.raises(BaseException) as catcher:
        loop.run(f(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        [cancel] = catcher.value.exceptions
        assert _flat_tb(catcher.value) == ["test_invalid_yield"]
        assert _flat_tb(cancel) == ["f", "g"]
    else:
        cancel = catcher.value
        assert _flat_tb(cancel) == ["test_invalid_yield", "f", "g"]
    assert type(cancel) is tinyio.CancelledError
    assert "Invalid yield" in str(cancel)


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
            assert _flat_tb(raising) == [workflow.__name__, "cancel", "cancel2"]
            assert type(cancelled) is tinyio.CancelledError
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
        cancelled_context = value.__context__
        assert cancelled_context is value.__cause__
        assert type(cancelled_context) is tinyio.CancelledError
        assert _flat_tb(cancelled_context) == ["blocking_operation", "sub_blocking_operation"]


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
        while True:
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
        while True:
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

    def _invalid(coro, value):
        del coro, value
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


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_keyboard_interrupt_while_waiting(exception_group):
    """Tests an error occurring from within the loop itself while waiting for a thread."""

    # This sends a keyboard interrupt signal to the main thread
    # as soon as the main thread has started the loop.
    ev = threading.Event()

    def foo():
        ev.wait()
        os.kill(os.getpid(), signal.SIGINT)

    t = threading.Thread(target=foo)
    t.start()

    # This simulates work in a blocking thread
    did_cleanup = False

    def _blocking_work():
        nonlocal did_cleanup
        try:
            ev.set()
            for _ in range(20):
                time.sleep(0.1)
        except tinyio.CancelledError:
            did_cleanup = True
            raise

    # The tinyio entry point that will wait for the blocking work
    def _f():
        yield [tinyio.run_in_thread(_blocking_work)]

    loop = tinyio.Loop()
    with pytest.raises(BaseException) as catcher:
        loop.run(_f(), exception_group)

    t.join()

    assert did_cleanup
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        [keyboard] = catcher.value.exceptions
        assert type(keyboard) is KeyboardInterrupt
    elif exception_group is None:
        keyboard = catcher.value
        assert type(keyboard) is KeyboardInterrupt


def test_usage_error_loop_already_running():
    def outer():
        yield inner()

    def inner():
        # Try to run the loop again while it's already running
        loop.run(dummy())
        yield

    def dummy():
        yield

    loop = tinyio.Loop()
    with pytest.raises(RuntimeError) as catcher:
        loop.run(outer())

    assert str(catcher.value) == "Cannot call `tinyio.Loop().run` whilst the loop is currently running."
    assert _flat_tb(catcher.value) == ["test_usage_error_loop_already_running", "outer", "inner"]


def test_usage_error_not_a_coroutine():
    def not_a_coro():
        return 42

    loop = tinyio.Loop()
    with pytest.raises(ValueError) as catcher:
        loop.run(not_a_coro)  # pyright: ignore[reportArgumentType]

    assert "not a coroutine" in str(catcher.value)
    assert _flat_tb(catcher.value) == ["test_usage_error_not_a_coroutine"]


@pytest.mark.parametrize("num_yields", (0, 1, 2, 20))
def test_usage_error_already_started_generator(num_yields):
    def coro():
        for _ in range(num_yields):
            yield

    gen = coro()
    with contextlib.suppress(StopIteration) if num_yields == 0 else contextlib.nullcontext():
        next(gen)  # Start the generator

    loop = tinyio.Loop()
    with pytest.raises(ValueError) as catcher:
        loop.run(gen)

    assert "generator that has already started" in str(catcher.value)
    assert _flat_tb(catcher.value) == ["test_usage_error_already_started_generator"]


def test_usage_error_event_bool():
    def outer():
        yield inner()

    def inner():
        event = tinyio.Event()
        if event:  # pyright: ignore[reportGeneralTypeIssues]
            pass
        yield

    loop = tinyio.Loop()
    with pytest.raises(TypeError) as catcher:
        loop.run(outer())

    assert "Cannot convert `tinyio.Event` to boolean" in str(catcher.value)
    assert "event.is_set()" in str(catcher.value)
    assert _flat_tb(catcher.value) == ["test_usage_error_event_bool", "outer", "inner", "__bool__"]


def test_usage_error_semaphore_non_positive():
    def outer():
        yield inner()

    def inner():
        tinyio.Semaphore(value=0)
        yield

    loop = tinyio.Loop()
    with pytest.raises(ValueError) as catcher:
        loop.run(outer())

    assert "`tinyio.Semaphore(value=...)` must be positive" in str(catcher.value)
    assert _flat_tb(catcher.value) == ["test_usage_error_semaphore_non_positive", "outer", "inner", "__init__"]


def test_usage_error_semaphore_reuse():
    def outer():
        yield inner()

    def inner():
        semaphore = tinyio.Semaphore(value=1)
        ctx = yield semaphore()
        with ctx:
            pass
        # Try to reuse the same context manager
        with ctx:
            pass
        yield

    loop = tinyio.Loop()
    with pytest.raises(RuntimeError) as catcher:
        loop.run(outer())

    assert "Use a new `semaphore()` call in each `with (yield semaphore())`" in str(catcher.value)
    assert _flat_tb(catcher.value) == ["test_usage_error_semaphore_reuse", "outer", "inner", "__enter__"]


@pytest.mark.parametrize("exception_group", (None, False, True))
@pytest.mark.parametrize("finished", (False, True), ids=("started", "finished"))
def test_yield_already_started_or_finished_generator(exception_group, finished):
    def outer():
        yield inner()

    def inner():
        def child():
            yield

        gen = child()
        next(gen)  # Start the generator
        if finished:
            with pytest.raises(StopIteration):
                next(gen)  # Exhaust the generator
        yield gen

    loop = tinyio.Loop()
    with pytest.raises(BaseException) as catcher:
        loop.run(outer(), exception_group)
    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        [cancel] = catcher.value.exceptions
        assert _flat_tb(catcher.value) == ["test_yield_already_started_or_finished_generator"]
        assert _flat_tb(cancel) == ["outer", "inner"]
    else:
        cancel = catcher.value
        assert _flat_tb(cancel) == ["test_yield_already_started_or_finished_generator", "outer", "inner"]
    assert type(cancel) is tinyio.CancelledError
    assert "has already started" in str(cancel)


@pytest.mark.parametrize("exception_group", (None, False, True))
def test_usage_error_in_exception_group(exception_group):
    def outer():
        yield [valid(), invalid()]

    def valid():
        while True:
            yield

    def invalid():
        yield
        # This will raise a usage error
        tinyio.Semaphore(value=-1)
        yield

    loop = tinyio.Loop()
    with pytest.raises(BaseException) as catcher:
        loop.run(outer(), exception_group)

    if exception_group is True:
        assert type(catcher.value) is BaseExceptionGroup
        [value_error, cancelled] = catcher.value.exceptions
        assert type(value_error) is ValueError
        assert "`tinyio.Semaphore(value=...)` must be positive" in str(value_error)
        # With exception_group, the traceback is stitched and cleaned up
        # Ends at __init__ -> usage_error (tinyio code)
        assert _flat_tb(value_error) == ["outer", "invalid", "__init__"]
        assert type(cancelled) is tinyio.CancelledError
    else:
        value_error = catcher.value
        assert type(value_error) is ValueError
        assert "`tinyio.Semaphore(value=...)` must be positive" in str(value_error)
        # Without exception_group, includes the test function name
        assert _flat_tb(value_error) == ["test_usage_error_in_exception_group", "outer", "invalid", "__init__"]
