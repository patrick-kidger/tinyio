import tinyio


def _block(event1: tinyio.Event, event2: tinyio.Event, out):
    yield event1.wait()
    event2.set()
    return out


def _test_done_callback():
    out = []
    event1 = tinyio.Event()
    event2 = tinyio.Event()
    event3 = tinyio.Event()
    yield {tinyio.add_done_callback(_block(event2, event3, 1), out.append)}
    yield {tinyio.add_done_callback(_block(event1, event2, 2), out.append)}
    for _ in range(20):
        yield
    assert len(out) == 0
    event1.set()
    yield event3.wait()
    assert out == [2, 1]


def test_done_callback():
    loop = tinyio.Loop()
    loop.run(_test_done_callback())


# To reinstate if we ever reintroduce error callbacks.


# def _raises_after(event: tinyio.Event):
#     yield event.wait()
#     raise RuntimeError("Kaboom")


# def _test_error_callback(error_callback1, error_callback2):
#     event1 = tinyio.Event()
#     event2 = tinyio.Event()
#     yield {tinyio.add_done_callback(_raises_after(event1), _assert_false, error_callback1)}
#     yield {tinyio.add_done_callback(_raises_after(event2), _assert_false, error_callback2)}
#     event1.set()


# def test_error_callback():
#     loop = tinyio.Loop()
#     out1 = []
#     out2 = []
#     with pytest.raises(RuntimeError, match="Kaboom") as catcher:
#         loop.run(_test_error_callback(out1.append, out2.append))
#     assert len(out1) == 1 and out1[0] is catcher.value
#     assert len(out2) == 1 and type(out2[0]) is tinyio.CancelledError


# def test_error_callback_that_raises1():
#     loop = tinyio.Loop()
#     out = []
#     def error_callback(_):
#         raise ValueError("Eek")
#     with pytest.raises(ValueError, match="Eek"):
#         loop.run(_test_error_callback(error_callback, out.append))
#     assert len(out) == 1 and type(out[0]) is tinyio.CancelledError


# def test_error_callback_that_raises2():
#     loop = tinyio.Loop()
#     out = []
#     def error_callback(_):
#         raise ValueError("Eek")
#     with pytest.raises(BaseExceptionGroup) as catcher, pytest.warns(RuntimeWarning, match="leak"):
#         loop.run(_test_error_callback(out.append, error_callback))
#     [runtime, value] = catcher.value.exceptions
#     assert type(runtime) is RuntimeError and str(runtime) == "Kaboom"
#     assert type(value) is ValueError and str(value) == "Eek"
