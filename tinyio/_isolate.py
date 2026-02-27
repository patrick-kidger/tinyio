from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Concatenate, ParamSpec, TypeVar

import tinyio


_P = ParamSpec("_P")
_T = TypeVar("_T")
_R = TypeVar("_R")


def _dupe(coro: tinyio.Coro[_T]) -> tuple[tinyio.Coro[None], tinyio.Coro[_T]]:
    """Takes a coro assumed to be scheduled on an event loop, and returns:

    - a new coroutine that should be scheduled in the background of the same loop;
    - a new coroutine that can be scheduled anywhere at all (typically a new loop), and
        will return the same value as the original coroutine.

    Thus, this is a pipe through which two event loops can talk to one another.
    """
    pipe = []
    done = tinyio.Event()
    failed = tinyio.Event()

    def put_on_old_loop():
        try:
            out = yield coro
        except BaseException:
            failed.set()
            done.set()
            raise
        else:
            pipe.append(out)
            done.set()

    def put_on_new_loop():
        yield done.wait()
        if failed.is_set():
            raise RuntimeError("Could not get input as underlying coroutine failed.")
        else:
            return pipe[0]

    return put_on_old_loop(), put_on_new_loop()


def _nest(coro: tinyio.Coro[_R], exception_group: None | bool = None) -> tinyio.Coro[_R]:
    """Runs one tinyio event loop within another.

    The outer loop will be in control of the stepping. The inner loop will have a
    separate collection of coroutines, which will be grouped and mutually shut down if
    one of them produces an error. Thus, this provides a way to isolate a group of
    coroutines within a broader collection.
    """
    with tinyio.Loop().runtime(coro, exception_group) as gen:
        while True:
            try:
                wait = next(gen)
            except StopIteration as e:
                return e.value
            if wait is None:
                yield
            else:
                yield tinyio.run_in_thread(wait)


def isolate(
    fn: Callable[..., tinyio.Coro[_R]],
    cleanup: Callable[[BaseException], tinyio.Coro[_R]],
    /,
    *args: tinyio.Coro,
    exception_group: None | bool = None,
) -> tinyio.Coro[tuple[_R, bool]]:
    """Runs a coroutine in an isolated event loop, and if it fails then cleanup is ran.

    **Arguments:**

    - `fn`: a function that returns a tinyio coroutine. Will be called as `fn(*args)` in order to get the coroutine to
        run. All coroutines that it depends on must be passed as `*args` (so that communication can be established
        between the two loops).
    - `cleanup`: if `fn(*args)` raises an error, then `cleanup(exception)` should provide a coroutine that can be called
        to clean things up.
    - `*args`: all coroutines that `fn` depends upon.

    **Returns:**

    A 2-tuple:

    - the first element is either the result of `fn(*args)` or `cleanup(exception)`.
    - whether `fn(*args)` succeeded or failed.
    """
    if len(args) > 0:
        olds, news = zip(*map(_dupe, args), strict=True)
    else:
        olds, news = [], []
    yield set(olds)
    try:
        # This `yield from` is load bearing! We must not allow the tinyio event loop to
        # interpose itself between the exception arising out of `fn(*news)`, and the
        # current stack frame. Otherwise we would get a `CancelledError` here instead.
        return (yield from _nest(fn(*news), exception_group=exception_group)), True
    except BaseException as e:
        return (yield cleanup(e)), False


# Stand back, some typing hackery required.
if TYPE_CHECKING:

    def _fn_signature(*args: tinyio.Coro[_T], exception_group: None | bool = None): ...

    def _make_isolate(
        fn: Callable[_P, Any],
    ) -> Callable[
        Concatenate[Callable[_P, tinyio.Coro[_R]], Callable[[BaseException], tinyio.Coro[_R]], _P],
        tinyio.Coro[tuple[_R, bool]],
    ]: ...

    isolate = _make_isolate(_fn_signature)
    del _fn_signature, _make_isolate
