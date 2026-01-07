import collections as co
import dataclasses
import enum
import graphlib
import heapq
import threading
import time
import traceback
import types
import warnings
import weakref
from collections.abc import Generator
from typing import Any, TypeAlias, TypeVar


#
# Public API: loop implementation
#
# The main logic is that each time coroutine yields, we create a `_WaitingFor` object which holds a counter for how many
# things it is waiting on before it can wake up. Once this counter hits zero, the `_WaitingFor` object schedules the
# coroutine back on the loop.
# Counters can be decremented in three ways: another coroutine finishes, an `Event.set()` is triggered, or a timeout in
# `Event.wait(timeout=...)` is triggered.
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
        if not isinstance(coro, Generator):
            raise ValueError("Invalid input `coro`, which is not a coroutine (a function using `yield` statements).")
        queue: co.deque[_Todo] = co.deque()
        queue.appendleft(_Todo(coro, None))
        waiting_on = dict[Coro, list[_WaitingFor]]()
        waiting_on[coro] = []
        # Loop invariant: `{x.coro for x in queue}.issubset(set(waiting_on.keys()))`
        wait_heap: list[_Wait] = []
        wake_loop = threading.Event()
        wake_loop.set()
        current_coro_ref = [coro]
        # Loop invariant: `len(current_coro_ref) == 1`. It's not really load-bearing, it's just used for making a nice
        # traceback when we get an error.
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
                        while len(queue) == 0:
                            self._wait(wait_heap, wake_loop)
                            self._clear(wait_heap, wake_loop)
                            # These lines needs to be wrapped in a `len(queue)` check, as just because we've unblocked
                            # doesn't necessarily mean that we're ready to schedule a coroutine: we could have something
                            # like `yield [event1.wait(...), event2.wait(...)]`, and only one of the two has unblocked.
                else:
                    self._clear(wait_heap, wake_loop)
                todo = queue.pop()
                current_coro_ref[0] = todo.coro
                self._step(todo, queue, waiting_on, wait_heap, wake_loop)
            current_coro_ref[0] = coro
        except BaseException as e:
            _cleanup(e, waiting_on, current_coro_ref, exception_group)
            raise  # if not raising an `exception_group`
        return self._results[coro]

    @staticmethod
    def _check_cycle(waiting_on, coro):
        sorter = graphlib.TopologicalSorter()
        for k, v in waiting_on.items():
            for vi in v:
                sorter.add(k, vi.coro)
        try:
            sorter.prepare()
        except graphlib.CycleError:
            coro.throw(RuntimeError("Cycle detected in `tinyio` loop. Cancelling all coroutines."))

    @staticmethod
    def _wait(wait_heap: list["_Wait"], wake_loop: threading.Event):
        timeout = None
        while len(wait_heap) > 0:
            soonest = wait_heap[0]
            assert soonest.timeout_in_seconds is not None
            if soonest.state == _WaitState.DONE:
                heapq.heappop(wait_heap)
            else:
                timeout = soonest.timeout_in_seconds - time.monotonic()
                break
        wake_loop.wait(timeout=timeout)

    @staticmethod
    def _clear(wait_heap: list["_Wait"], wake_loop: threading.Event):
        wake_loop.clear()
        while len(wait_heap) > 0:
            soonest = wait_heap[0]
            assert soonest.timeout_in_seconds is not None
            if soonest.state == _WaitState.DONE:
                heapq.heappop(wait_heap)
            elif soonest.timeout_in_seconds <= time.monotonic():
                heapq.heappop(wait_heap)
                soonest.notify_from_timeout()
            else:
                break

    def _step(
        self,
        todo: "_Todo",
        queue: co.deque["_Todo"],
        waiting_on: dict[Coro, list["_WaitingFor"]],
        wait_heap: list["_Wait"],
        wake_loop: threading.Event,
    ) -> None:
        try:
            out = todo.coro.send(todo.value)
        except StopIteration as e:
            self._results[todo.coro] = e.value
            for waiting_for in waiting_on.pop(todo.coro):
                waiting_for.decrement()
        else:
            original_out = out
            if type(out) is list and len(out) == 0:
                out = None
            if isinstance(out, (_Wait, Generator)):
                out = [out]
            match out:
                case None:
                    # original_out will either be `None` or `[]`.
                    queue.appendleft(_Todo(todo.coro, original_out))
                case set():
                    for out_i in out:
                        if isinstance(out_i, Generator):
                            if out_i not in self._results.keys() and out_i not in waiting_on.keys():
                                if out_i.gi_frame is None:  # pyright: ignore[reportAttributeAccessIssue]
                                    todo.coro.throw(_already_finished(out_i))
                                queue.appendleft(_Todo(out_i, None))
                                waiting_on[out_i] = []
                        else:
                            assert not isinstance(out_i, _Wait)
                            todo.coro.throw(_invalid(original_out))
                    queue.appendleft(_Todo(todo.coro, None))
                case list():
                    waiting_for = _WaitingFor(len(out), todo.coro, original_out, wake_loop, self._results, queue)
                    for out_i in out:
                        if isinstance(out_i, Generator):
                            if out_i in self._results.keys():
                                waiting_for.decrement()
                            elif out_i in waiting_on.keys():
                                waiting_on[out_i].append(waiting_for)
                            else:
                                if out_i.gi_frame is None:  # pyright: ignore[reportAttributeAccessIssue]
                                    todo.coro.throw(_already_finished(out_i))
                                queue.appendleft(_Todo(out_i, None))
                                waiting_on[out_i] = [waiting_for]
                        elif isinstance(out_i, _Wait):
                            out_i.register(waiting_for)
                            if out_i.timeout_in_seconds is not None:
                                heapq.heappush(wait_heap, out_i)
                        else:
                            todo.coro.throw(_invalid(original_out))
                case _:
                    todo.coro.throw(_invalid(original_out))


class CancelledError(BaseException):
    """Raised when a `tinyio` coroutine is cancelled due an error in another coroutine."""


CancelledError.__module__ = "tinyio"


#
# Loop internals, in particular events and waiting
#


@dataclasses.dataclass(frozen=True)
class _Todo:
    coro: Coro
    value: Any


# We need at least some use of locks, as `Event`s are public objects that may interact with user threads. If the
# internals of our event/wait/waitingfor mechanisms are modified concurrently then it would be very easy for things to
# go wrong.
# In particular note that our event loop is one actor that is making modifications, in addition to user threads.
# For this reason it doesn't suffice to just have a lock around `Event.{set, clear}`.
# For simplicity, we simply guard all entries into the event/wait/waitingfor mechanism with a single lock. We could try
# to use some other locking strategy but that seems error-prone.
_global_event_lock = threading.RLock()


@dataclasses.dataclass(frozen=False)
class _WaitingFor:
    counter: int
    coro: Coro
    out: "_Wait | Coro | list[_Wait | Coro]"
    wake_loop: threading.Event
    results: weakref.WeakKeyDictionary[Coro, Any]
    queue: co.deque[_Todo]

    def __post_init__(self):
        assert self.counter > 0

    def increment(self):
        with _global_event_lock:
            # This assert is valid as our only caller is `_Wait.unnotify_from_event`, which will only have a reference
            # to us if we haven't completed yet -- otherwise we'd have already called its `_Wait.cleanup` method.
            assert self.counter != 0
            self.counter += 1

    def decrement(self):
        # We need a lock here as this may be called simultaneously between our event loop and via `Event.set`.
        # (Though `Event.set` has its only internal lock, that doesn't cover the event loop as well.)
        with _global_event_lock:
            assert self.counter > 0
            self.counter -= 1
            if self.counter == 0:
                match self.out:
                    case None:
                        result = None
                        waits = []
                    case _Wait():
                        result = None
                        waits = [self.out]
                    case Generator():
                        result = self.results[self.out]
                        waits = []
                    case list():
                        result = [None if isinstance(out_i, _Wait) else self.results[out_i] for out_i in self.out]
                        waits = [out_i for out_i in self.out if isinstance(out_i, _Wait)]
                    case _:
                        assert False
                for wait in waits:
                    wait.cleanup()
                self.queue.appendleft(_Todo(self.coro, result))
                # If we're callling this function from a thread, and the main event loop is blocked, then use this to
                # notify the main event loop that it can wake up.
                self.wake_loop.set()


class _WaitState(enum.Enum):
    INITIALISED = "initialised"
    REGISTERED = "registered"
    NOTIFIED_EVENT = "notified_event"
    NOTIFIED_TIMEOUT = "notified_timeout"
    DONE = "done"


class _Wait:
    def __init__(self, event: "Event", timeout_in_seconds: None | int | float):
        self._event = event
        self._timeout_in_seconds = timeout_in_seconds
        self._waiting_for = None
        self.state = _WaitState.INITIALISED

    # This is basically just a second `__init__` method. We're not really initialised until this has been called
    # precisely once as well. The reason we have two is that an end-user creates us during `Event.wait()`, and then we
    # need to register on the event loop.
    def register(self, waiting_for: "_WaitingFor") -> None:
        with _global_event_lock:
            assert self.state is _WaitState.INITIALISED
            assert self._waiting_for is None
            assert self._event is not None
            self.state = _WaitState.REGISTERED
            if self._timeout_in_seconds is None:
                self.timeout_in_seconds = None
            else:
                self.timeout_in_seconds = time.monotonic() + self._timeout_in_seconds
            self._waiting_for = waiting_for
            self._event._waits[self] = None
            if self._event.is_set():
                self.notify_from_event()

    def notify_from_event(self):
        with _global_event_lock:
            # We cannot have `NOTIFIED_EVENT` as our event will have toggled its internal state to `True` as part of
            # calling us, and so future `Event.set()` calls will not call `.notify_from_event`.
            # We cannot have `DONE` as this is only set during `.cleanup()`, and at that point we deregister from
            # `self._event._waits`.
            assert self.state in {_WaitState.REGISTERED, _WaitState.NOTIFIED_TIMEOUT}
            assert self._waiting_for is not None
            if self.state == _WaitState.REGISTERED:
                self.state = _WaitState.NOTIFIED_EVENT
                self._waiting_for.decrement()

    def notify_from_timeout(self):
        with _global_event_lock:
            assert self.state in {_WaitState.REGISTERED, _WaitState.NOTIFIED_EVENT}
            assert self._waiting_for is not None
            is_registered = self.state == _WaitState.REGISTERED
            self.state = _WaitState.NOTIFIED_TIMEOUT  # Override `NOTIFIED_EVENT` in case we `unnotify_from_event` later
            if is_registered:
                self._waiting_for.decrement()

    def unnotify_from_event(self):
        with _global_event_lock:
            assert self.state in {_WaitState.NOTIFIED_EVENT, _WaitState.NOTIFIED_TIMEOUT}
            assert self._waiting_for is not None
            # But ignore un-notifies if we've already triggered our timeout.
            if self.state is _WaitState.NOTIFIED_EVENT:
                self.state = _WaitState.REGISTERED
                self._waiting_for.increment()

    def cleanup(self):
        with _global_event_lock:
            assert self.state in {_WaitState.NOTIFIED_EVENT, _WaitState.NOTIFIED_TIMEOUT}
            assert self._waiting_for is not None
            assert self._event is not None
            self.state = _WaitState.DONE
            self._waiting_for = None  # For GC purposes.
            del self._event._waits[self]
            self._event = None  # For GC purposes.

    # For `heapq` to work.
    def __lt__(self, other):
        return self.timeout_in_seconds < other.timeout_in_seconds


class Event:
    """A marker that something has happened."""

    def __init__(self):
        self._value = False
        self._waits = dict[_Wait, None]()

    def is_set(self):
        return self._value

    def set(self):
        with _global_event_lock:
            if not self._value:
                for wait in self._waits.copy().keys():
                    wait.notify_from_event()
                self._value = True

    def clear(self):
        with _global_event_lock:
            if self._value:
                for wait in self._waits.keys():
                    wait.unnotify_from_event()
                self._value = False

    def wait(self, timeout_in_seconds: None | int | float = None) -> Coro[None]:
        yield _Wait(self, timeout_in_seconds)

    def __bool__(self):
        raise TypeError("Cannot convert `tinyio.Event` to boolean. Did you mean `event.is_set()`?")


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
    tb = base_e.__traceback__
    while tb is not None:
        tb_next = tb.tb_next
        if tb_next is None:
            break
        else:
            tb = tb_next
    if tb is None:
        module_e = ""
    else:
        module_e = tb.tb_frame.f_globals.get("__name__", "")
    if not module_e.startswith("tinyio."):
        # 2 skipped frames:
        # `self._step`
        # either `coro.throw(...)` or `todo.coro.send(todo.value)`
        # Don't skip them if the error was an internal error in tinyio, or a KeyboardInterrupt.
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


def _already_finished(out):
    return RuntimeError(
        f"The coroutine `{out}` has already finished. However it has not been seen by the `tinyio` loop before and as "
        "such does not have any result associated with it."
    )
