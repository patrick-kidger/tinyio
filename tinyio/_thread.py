import ctypes
import threading
from collections.abc import Callable
from typing import ParamSpec, TypeVar, cast

from ._core import Coro, CancelledError

_Params = ParamSpec("_Params")
_Return = TypeVar("_Return")


def run_in_thread(
    fn: Callable[_Params, _Return], /, *args: _Params.args, **kwargs: _Params.kwargs
) -> Coro[_Return]:
    """A `tinyio` coroutine for running the blocking function `fn(*args, **kwargs)` in a thread.

    If this coroutine is cancelled then the cancellation will be raised in the thread as well; vice-versa if the
    function call raises an error then this will propagate to the coroutine.
    """

    is_exception = None
    result = None

    def target():
        nonlocal result, is_exception
        try:
            result = fn(*args, **kwargs)
            is_exception = False
        except BaseException as e:
            result = e
            is_exception = True

    t = threading.Thread(target=target)

    try:
        t.start()
        while is_exception is None:
            yield
    except BaseException as e:
        # We can end up here if an `tinyio.CancelledError` arises out of the `yield`, or from an exogeneous
        # `KeyboardInterrupt`, or from re-raising the error out of our thread.
        thread_id = t.ident
        assert thread_id is not None
        # Raise a `CancelledError` in the thread that is running the task. This allows the thread to do any cleanup.
        # This is not readily supported and needs to be done via ctypes, see: https://gist.github.com/liuw/2407154.
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), ctypes.py_object(CancelledError))
        t.join()
        # Our thread above has now completed.
        if is_exception and type(e) is CancelledError:
            # We were cancelled.
            #
            # Note that we raise this regardless of whether `result` is itself a `CancelledError`. It's probably the
            # error we triggered via `ctypes` above, but in principle the function may have caught that and done
            # something else.
            # Either way, we are but the humble purveyors of this message back to the event loop: raise it.
            result = cast(BaseException, result)
            context = result.__context__
            cause = result.__cause__
            try:
                raise result
            except BaseException as e:
                # try-and-immediately-except is a trick to remove the current frame from the traceback.
                # This smoothly links up the `run_in_thread` invocation with the frame in `target`, with no weird
                # `raise result` frame halfway through.
                # In addition we also need to preserve the `__context__` (and `__cause__`?) as the `raise` overwrites
                # this with the current context.
                # Curiously this doesn't seem to work if we unify this with the `else` branch of the `try/except/else`
                # below, despite ostensibly then being outside of the `except BaseException` context. Whatever, this
                # version works correctly!
                assert e.__traceback__ is not None
                e.__traceback__ = e.__traceback__.tb_next
                e.__context__ = context
                e.__cause__ = cause
                raise
        else:
            # Probably a `KeyboardInterrupt`, forward it on.
            raise
    else:
        if is_exception:
            try:
                raise cast(BaseException, result)
            except BaseException as e:
                assert e.__traceback__ is not None
                e.__traceback__ = e.__traceback__.tb_next
                raise
        else:
            return cast(_Return, result)
