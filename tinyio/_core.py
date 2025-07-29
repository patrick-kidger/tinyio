import collections as co
import dataclasses
import time
import traceback
import types
import warnings
import weakref
from collections.abc import Generator
from typing import Any, TypeAlias, TypeVar


#
# Loop implementation
#


_Return = TypeVar("_Return")
Coro: TypeAlias = Generator[Any, Any, _Return]


class Loop:
    """Event loop for running `tinyio`-style coroutines."""

    def __init__(self):
        # Keep around the results with weakrefs.
        # This makes it possible to perform multiple `.run`s, with coroutines that may internally await on the same
        # coroutines as each other.
        # It's a weakref as if no-one else has access to them then they cannot appear in our event loop, so we don't
        # need to keep their results around for the above use-case.
        self._results = weakref.WeakKeyDictionary()

    def run(self, coro: Coro[_Return], exception_group: None | bool = None) -> _Return:
        """Run the specified coroutine in the event loop.

        **Arguments:**

        - `coro`: a Python coroutine to run; it may yield `None`, other coroutines, or lists-of-coroutines.
        - `exception_group`: in the event of an error in one of the coroutines (which will cancel all other coroutines
            and shut down the loop), then this determines the kind of exception raised out of the loop:
            - if `False` then raise just that error, silently ignoring any errors that occur when cancelling the other
                coroutines.
            - if `True` then always raise a `{Base}ExceptionGroup`, whose first sub-exception will be the original
                error, and whose later sub-exceptions will be any errors that occur whilst cancelling the other
                coroutines. (Including all the `tinyio.CancelledError`s that indicate successful cancellation.)
            - if `None` (the default) then raise just the original error if all other coroutines shut down successfully,
                and raise a `{Base}ExceptionGroup` if any other coroutine raises an exception during shutdown.
                (Excluding all the `tinyio.CancelledError`s that indicate successful cancellation.)

        **Returns:**

        The final `return` from `coro`.
        """
        queue: co.deque[_Todo] = co.deque()
        waiting_on: dict[Coro, list[Coro]] = {}
        waiting_for: dict[Coro, _WaitingFor] = {}
        queue.appendleft(_Todo(coro, None))
        waiting_on[coro] = []
        # Loop invariant: always holds a single element. It's not really load-bearing, it's just used for making a nice
        # traceback when we get an error.
        current_coro_ref = [coro]
        try:
            while len(queue) > 0:
                todo = queue.pop()
                current_coro_ref[0] = todo.coro
                self._step(todo, queue, waiting_on, waiting_for)
            current_coro_ref[0] = coro
            self._check_cycle(waiting_on, coro)
        except BaseException as e:
            _cleanup(e, waiting_on, current_coro_ref, exception_group)
            raise  # if not raising an `exception_group`
        return self._results[coro]

    def _check_cycle(self, waiting_on, coro):
        del self
        if len(waiting_on) != 0:
            import graphlib

            try:
                graphlib.TopologicalSorter(waiting_on).prepare()
            except graphlib.CycleError:
                coro.throw(RuntimeError("Cycle detected in `tinyio` loop. Cancelling all coroutines."))
            else:
                assert False, "Something has gone wrong inside the `tinyio` loop."

    def _step(
        self,
        todo: "_Todo",
        queue: co.deque["_Todo"],
        waiting_on: dict[Coro, list[Coro]],
        waiting_for: dict[Coro, "_WaitingFor"],
    ) -> None:
        try:
            out = todo.coro.send(todo.value)
        except StopIteration as e:
            self._results[todo.coro] = e.value
            for coro in waiting_on.pop(todo.coro):
                coro_waiting_for = waiting_for[coro]
                coro_waiting_for.count -= 1
                if coro_waiting_for.count == 0:
                    del waiting_for[coro]
                    if isinstance(coro_waiting_for.coros, list):
                        value = [self._results[g] for g in coro_waiting_for.coros]
                    else:
                        value = self._results[coro_waiting_for.coros]
                    queue.appendleft(_Todo(coro, value))
        else:
            original_out = out
            if single_coroutine := isinstance(out, Generator):
                out = [out]
            match out:
                case None:
                    queue.appendleft(_Todo(todo.coro, None))
                case set():
                    queue.appendleft(_Todo(todo.coro, None))
                    for out_i in out:
                        if not isinstance(out_i, Generator):
                            todo.coro.throw(_invalid(out))
                        if out_i not in waiting_on.keys():
                            queue.appendleft(_Todo(out_i, None))
                            waiting_on[out_i] = []
                case list():
                    num_done = 0
                    for out_i in out:
                        if not isinstance(out_i, Generator):
                            # Not just a direct `raise` as we need to shut down this coroutine too + this gives a
                            # nicer stack trace.
                            todo.coro.throw(_invalid(out))
                        if out_i in self._results.keys():
                            # Already finished.
                            num_done += 1
                        elif out_i in waiting_on.keys():
                            # Already in queue; someone else is waiting on this coroutine too.
                            waiting_on[out_i].append(todo.coro)
                        else:
                            # New coroutine
                            waiting_on[out_i] = [todo.coro]
                            queue.appendleft(_Todo(out_i, None))
                    if num_done == len(out):
                        # All requested coroutines already finished; immediately queue up original coroutine.
                        # again.
                        if single_coroutine:
                            queue.appendleft(_Todo(todo.coro, self._results[original_out]))
                        else:
                            queue.appendleft(_Todo(todo.coro, [self._results[out_i] for out_i in out]))
                    else:
                        assert todo.coro not in waiting_for.keys()
                        waiting_for[todo.coro] = _WaitingFor(
                            len(out) - num_done, original_out if single_coroutine else out
                        )
                case _:
                    todo.coro.throw(_invalid(out))


class CancelledError(BaseException):
    """Raised when a `tinyio` coroutine is cancelled due an error in another coroutine."""


Loop.__module__ = "tinyio"
CancelledError.__module__ = "tinyio"


def sleep(delay_in_seconds: float) -> Coro[None]:
    """Sleeps for `delay_in_seconds` many seconds without blocking the event loop."""
    timeout = time.monotonic() + delay_in_seconds
    while True:
        if time.monotonic() > timeout:
            break
        yield


@dataclasses.dataclass(frozen=True)
class _Todo:
    coro: Coro
    value: Any


@dataclasses.dataclass(frozen=False)
class _WaitingFor:
    count: int
    coros: Coro | list[Coro]


#
# Error handling
#


def _strip_frames(e: BaseException, n: int):
    tb = e.__traceback__
    for _ in range(n):
        if tb is not None:
            tb = tb.tb_next
    return e.with_traceback(tb)


def _cleanup(
    base_e: BaseException,
    waiting_on: dict[Coro, list[Coro]],
    current_coro_ref: list[Coro],
    exception_group: None | bool,
):
    # Oh no! Time to shut everything down. We can get here in two different ways:
    # - One of our coroutines raised an error internally (including being interrupted with a `KeyboardInterrupt`).
    # - An exogenous `KeyboardInterrupt` occurred whilst we were within the loop itself.
    [current_coro] = current_coro_ref
    # First, stop all the coroutines.
    cancellation_errors: dict[Coro, BaseException] = {}
    other_errors: dict[Coro, BaseException] = {}
    for coro in waiting_on.keys():
        # We do not have an `if coro is current_coro: continue` clause here. It may indeed be the case that
        # `current_coro` was the the origin of the current error (or the one on which we called `.throw` on in a
        # few cases), so it has already been shut down. However it may also be the case that there was an exogenous
        # `KeyboardInterrupt` whilst within the tinyio loop itself, in which case we do need to shut this one down
        # as well.
        try:
            out = coro.throw(CancelledError)
        except CancelledError as e:
            # Skipped frame is the `coro.throw` above.
            cancellation_errors[coro] = _strip_frames(e, 1)
            continue
        except StopIteration as e:
            what_did = f"returned `{e.value}`."
        except BaseException as e:
            # Skipped frame is the `coro.throw` above.
            other_errors[coro] = _strip_frames(e, 1)
            details = "".join(traceback.format_exception_only(e)).strip()
            what_did = f"raised the exception `{details}`."
        else:
            what_did = f"yielded `{out}`."
        warnings.warn(
            f"Coroutine `{coro}` did not respond properly to cancellation on receiving a "
            "`tinyio.CancelledError`, and so a resource leak may have occurred. The coroutine is expected to "
            "propagate the `tinyio.CancelledError` to indicate success in cleaning up resources. Instead, the "
            f"coroutine {what_did}\n",
            category=RuntimeWarning,
            stacklevel=3,
        )
    # 2 skipped frames:
    # `self._step`
    # either `coro.throw(...)` or `todo.coro.send(todo.value)`
    _strip_frames(base_e, 2)  # pyright: ignore[reportPossiblyUnboundVariable]
    # Next: bit of a heuristic, but it is pretty common to only have one thing waiting on you, so stitch together
    # their tracebacks as far as we can. Thinking about specifically `current_coro`:
    #
    # - If `current_coro` was the source of the error then our `coro.throw(CancelledError)` above will return an
    #   exception with zero frames in its traceback (well it starts with a single frame for
    #   `coro.throw(CancelledError)`, but this immediately gets stripped above). So we begin by appending nothing here,
    #   which is what we want.
    # - If this was an exogenous `KeyboardInterrupt` whilst we were within the loop itself, then we'll append the
    #   stack from cancelling `current_coro`, which again is what we want.
    #
    # And then after that we just keep working our way up appending the cancellation tracebacks for each coroutine in
    # turn.
    coro = current_coro
    tb = base_e.__traceback__  # pyright: ignore[reportPossiblyUnboundVariable]
    while True:
        next_e = cancellation_errors.pop(coro, None)
        if next_e is None:
            break  # This coroutine responded improperly; don't try to go any further.
        else:
            flat_tb = []
            tb_ = next_e.__traceback__
            while tb_ is not None:
                flat_tb.append(tb_)
                tb_ = tb_.tb_next
            for tb_ in reversed(flat_tb):
                tb = types.TracebackType(tb, tb_.tb_frame, tb_.tb_lasti, tb_.tb_lineno)
        if len(waiting_on[coro]) != 1:
            # Either no-one is waiting on us and we're at the root, or multiple are waiting and we can't uniquely append
            # tracebacks any more.
            break
        [coro] = waiting_on[coro]
    base_e.with_traceback(tb)  # pyright: ignore[reportPossiblyUnboundVariable]
    if exception_group is None:
        exception_group = len(other_errors) > 0
        cancellation_errors.clear()
    if exception_group:
        # Most cancellation errors are single frame tracebacks corresponding to the underlying generator.
        # A handful of them may be more interesting than this, e.g. if there is a `yield from` or if it's
        # `run_in_thread` which begins with the traceback from within the thread.
        # Bump these more-interesting ones to the top.
        interesting_cancellation_errors = []
        other_cancellation_errors = []
        for e in cancellation_errors.values():
            more_than_one_frame = e.__traceback__ is not None and e.__traceback__.tb_next is not None
            has_context = e.__context__ is not None
            if more_than_one_frame or has_context:
                interesting_cancellation_errors.append(e)
            else:
                other_cancellation_errors.append(e)
        raise BaseExceptionGroup(
            "An error occured running a `tinyio` loop.\nThe first exception below is the original error. Since it is "
            "common for each coroutine to only have one other coroutine waiting on it, then we have stitched together "
            "their tracebacks for as long as that is possible.\n"
            "The other exceptions are all exceptions that occurred whilst stopping the other coroutines.\n"
            "(For a debugger that allows for navigating within exception groups, try "
            "`https://github.com/patrick-kidger/patdb`.)\n",
            [base_e, *other_errors.values(), *interesting_cancellation_errors, *other_cancellation_errors],  # pyright: ignore[reportPossiblyUnboundVariable]
        )
    # else let the parent `raise` the original error.


def _invalid(out):
    msg = f"Invalid yield {out}. Must be either `None`, a coroutine, or a list of coroutines."
    if type(out) is tuple:
        # We could support this but I find the `[]` visually distinctive.
        msg += (
            " In particular to wait on multiple coroutines (a 'gather'), then the syntax is `yield [foo, bar]`, "
            "not `yield foo, bar`."
        )
    return RuntimeError(msg)
