import collections as co
import dataclasses
import graphlib
import threading
import traceback
import types
import warnings
import weakref
from collections.abc import Generator
from typing import Any, TypeAlias, TypeVar, cast


#
# Loop implementation
#
# The main logic is that coroutines produce `_WaitingFor` objects which schedule them back on the loop once all the
# coroutines they are waiting on have notified them of completion.
# In addition we special-case `Event`s in the loop, so that threads can use them to notify the loop of completion,
# without the loop needing to poll for this.
#


_Return = TypeVar("_Return")
Coro: TypeAlias = Generator[Any, Any, _Return]


@dataclasses.dataclass(frozen=True)
class _Todo:
    coro: Coro
    value: Any


class Event:
    """A marker that something has happened."""

    def __init__(self):
        self._value = False
        self._waiting_fors = dict[Coro, _WaitingFor]()
        self._lock = threading.Lock()

    def is_set(self):
        return self._value

    def set(self):
        # This is a user-visible class, so they could be doing anything with it - in particular multiple threads may
        # call `.set` simultaneously.
        with self._lock:
            if not self._value:
                to_delete = []
                for coro, waiting_for in self._waiting_fors.items():
                    waiting_for.decrement()
                    if waiting_for.counter == 0:
                        to_delete.append(coro)
                # Deleting them here isn't necessary for logical correctness, but is done just to allow things to be
                # GC'd. As an `Event` is user-supplied then it could have anything holding references to it, so this is
                # the appropriate point to allow our internals to be GC'd as soon as possible.
                for coro in to_delete:
                    del self._waiting_fors[coro]
            self._value = True

    def wait(self) -> Coro[None]:
        # Lie about the return type, this is an implementation detail that should otherwise feel like a coroutine.
        return _Wait(self, used=False)  # pyright: ignore[reportReturnType]

    def _register(self, waiting_for: "_WaitingFor") -> None:
        # Here we need the lock in case `self.set()` is being called at the same time as this method. Importantly, this
        # is the same lock as is used in `self.set()`.
        with self._lock:
            if self._value:
                waiting_for.decrement()
            else:
                assert waiting_for.coro not in self._waiting_fors.keys()
                self._waiting_fors[waiting_for.coro] = waiting_for

    def __bool__(self):
        raise TypeError("Cannot convert `tinyio.Event` to boolean. Did you mean `event.is_set()`?")


@dataclasses.dataclass(frozen=False)
class _Wait:
    event: Event
    used: bool


@dataclasses.dataclass(frozen=False)
class _WaitingFor:
    counter: int
    coro: Coro
    out: _Wait | Coro | list[_Wait | Coro]
    wake_loop: threading.Event
    results: weakref.WeakKeyDictionary[Coro, Any]
    queue: co.deque[_Todo]
    lock: threading.Lock

    def decrement(self):
        # We need a lock here as this may be called simultaneously between our event loop and via `Event.set`.
        # (Though `Event.set` has its only internal lock, that doesn't cover the event loop as well.)
        with self.lock:
            assert self.counter > 0
            self.counter -= 1
            schedule = self.counter == 0
        if schedule:
            match self.out:
                case None:
                    result = None
                case _Wait():
                    result = None
                case Generator():
                    result = self.results[self.out]
                case list():
                    result = [None if isinstance(out_i, _Wait) else self.results[out_i] for out_i in self.out]
                case _:
                    assert False
            self.queue.appendleft(_Todo(self.coro, result))
            # If we're callling this function from a thread, and the main event loop is blocked, then use this to notify
            # the main event loop that it can wake up.
            self.wake_loop.set()


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
        if isinstance(coro, _Wait):

            def gen(coro=coro):
                yield coro

            coro = cast(Coro, gen())
        if not isinstance(coro, Generator):
            raise ValueError("Invalid input `coro`, which is not a coroutine (a function using `yield` statements).")
        queue: co.deque[_Todo] = co.deque()
        queue.appendleft(_Todo(coro, None))
        waiting_on = dict[Coro, list[_WaitingFor]]()
        waiting_on[coro] = []
        # Loop invariant: `{x.coro for x in queue}.issubset(set(waiting_on.keys()))`
        wake_loop = threading.Event()
        wake_loop.set()
        # Loop invariant: `len(current_coro_ref) == 1`. It's not really load-bearing, it's just used for making a nice
        # traceback when we get an error.
        current_coro_ref = [coro]
        try:
            while True:
                if len(queue) == 0:
                    if len(waiting_on) == 0:
                        # We're done.
                        break
                    else:
                        # We might have a cycle bug...
                        self._check_cycle(waiting_on, coro)
                        # ...but hopefully we're just waiting on a thread or exogeneous event to unblock one of our
                        # coroutines.
                        wake_loop.wait()
                wake_loop.clear()
                todo = queue.pop()
                current_coro_ref[0] = todo.coro
                self._step(todo, queue, waiting_on, wake_loop)
            current_coro_ref[0] = coro
        except BaseException as e:
            _cleanup(e, waiting_on, current_coro_ref, exception_group)
            raise  # if not raising an `exception_group`
        return self._results[coro]

    def _check_cycle(self, waiting_on, coro):
        del self
        sorter = graphlib.TopologicalSorter({k: [vi.coro for vi in v] for k, v in waiting_on.items()})
        try:
            sorter.prepare()
        except graphlib.CycleError:
            coro.throw(RuntimeError("Cycle detected in `tinyio` loop. Cancelling all coroutines."))

    def _step(
        self, todo: _Todo, queue: co.deque[_Todo], waiting_on: dict[Coro, list[_WaitingFor]], wake_loop: threading.Event
    ) -> None:
        try:
            out = todo.coro.send(todo.value)
        except StopIteration as e:
            self._results[todo.coro] = e.value
            for waiting_for in waiting_on.pop(todo.coro):
                waiting_for.decrement()
        else:
            original_out = out
            if isinstance(out, (_Wait, Generator)):
                out = [out]
            match out:
                case None:
                    queue.appendleft(_Todo(todo.coro, None))
                case set():
                    for out_i in out:
                        if isinstance(out_i, Generator):
                            if out_i not in self._results.keys() and out_i not in waiting_on.keys():
                                queue.appendleft(_Todo(out_i, None))
                                waiting_on[out_i] = []
                        elif isinstance(out_i, _Wait):
                            # Scheduling an `event.wait()` in the background corresponds to doing nothing.
                            # We could also just replace this with error with a `pass`, as doing this is harmless.
                            # We make it an error just because that's probably better UX? Seems like a mistake.
                            todo.coro.throw(
                                RuntimeError(
                                    "Do not `yield {event.wait(), ...}`, it is meangless to both wait on an event and "
                                    "schedule it in the background."
                                )
                            )
                        else:
                            todo.coro.throw(_invalid(original_out))
                    queue.appendleft(_Todo(todo.coro, None))
                case list():
                    waiting_for = _WaitingFor(
                        len(out), todo.coro, original_out, wake_loop, self._results, queue, threading.Lock()
                    )
                    seen_events = set()
                    for out_i in out:
                        if isinstance(out_i, Generator):
                            if out_i in self._results.keys():
                                waiting_for.decrement()
                            elif out_i in waiting_on.keys():
                                waiting_on[out_i].append(waiting_for)
                            else:
                                queue.appendleft(_Todo(out_i, None))
                                waiting_on[out_i] = [waiting_for]
                        elif isinstance(out_i, _Wait):
                            if out_i.used:
                                # I don't think there's any actual harm in this, but it's a weird thing to do. We
                                # reserve the right for this to mean something more precise in the future.
                                todo.coro.throw(
                                    RuntimeError(
                                        "Do not yield the same `event.wait()` multiple times. Make a new `.wait()` "
                                        "call instead."
                                    )
                                )
                            out_i.used = True
                            if out_i.event in seen_events:
                                waiting_for.decrement()
                            else:
                                out_i.event._register(waiting_for)
                            seen_events.add(out_i.event)
                        else:
                            todo.coro.throw(_invalid(original_out))
                case _:
                    todo.coro.throw(_invalid(original_out))


class CancelledError(BaseException):
    """Raised when a `tinyio` coroutine is cancelled due an error in another coroutine."""


CancelledError.__module__ = "tinyio"


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
    waiting_on: dict[Coro, list[_WaitingFor]],
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
            if getattr(e, "__tinyio_no_warn__", False):
                continue
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
        [waiting_for] = waiting_on[coro]
        coro = waiting_for.coro
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
    msg = f"Invalid yield {out}. Must be either `None`, a coroutine, or a list/set of coroutines."
    if type(out) is tuple:
        # We could support this but I find the `[]` visually distinctive.
        msg += (
            " In particular to wait on multiple coroutines (a 'gather'), then the syntax is `yield [foo, bar]`, "
            "not `yield foo, bar`."
        )
    return RuntimeError(msg)
