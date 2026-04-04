from typing import Literal, TypeVar

from ._core import Coro, Event, Loop
from ._thread import run_in_thread


_T = TypeVar("_T")
_R = TypeVar("_R")


def copy(coro: Coro[_T]) -> Coro[Coro[_T]]:
    """Schedules a coroutine, and returns a new coroutine that returns the same value.

    Usage:
    ```python
    def your_coroutine():
        x1 = ...  # some tinyio coroutine
        x2 = yield tinyio.copy(x1)
        # `x2` is a brand-new coroutine that returns the same value as `x`:
        return1 = yield x1
        return2 = yield x2
        assert return1 is return2
    ```
    This works by scheduling the provided `coro` on the event loop, and then storing the output of the original
    coroutine once it completes.

    This function is useful primarily in conjunction with `tinyio.isolate`, to make information from the main loop
    available within the isolated loop.

    (More generally this function makes it possible to combine multiple `tinyio.Loop`s. Each individual coroutine can
    only be scheduled on a single loop, but this function makes it possible to create a fresh coroutine that produces
    the value returned by the first.)

    **Arguments:**

    - `coro`: the coroutine to copy.

    **Returns:**

    A coroutine that returns a copy of `coro`:
    """
    pipe = []
    schedule = Event()
    done = Event()

    def put_on_old_loop():
        # Don't actually `yield coro` until `put_on_new_loop` has started.
        # I don't know if that matters but it seems worth doing?
        yield schedule.wait()
        try:
            out = yield coro
        except BaseException as e:
            pipe.append((e, False))
            raise
        else:
            pipe.append((out, True))
        finally:
            done.set()

    def put_on_new_loop() -> Coro[_T]:
        schedule.set()
        yield done.wait()
        [[out, success]] = pipe
        if success:
            return out
        else:
            raise out

    yield {put_on_old_loop()}
    return put_on_new_loop()


def isolate(
    coro: Coro[_R], /, exception_group: None | bool = None
) -> Coro[tuple[_R, Literal[True]] | tuple[BaseException, Literal[False]]]:
    """Runs a coroutine in an isolated event loop, and if it (or any coroutines it yields) fails, then return the
    exception that occurred. (Cancelling all coroutines it created... but not cancelling the rest of the coroutines on
    the event loop.)

    Note that the coroutines it yields must all be *new* coroutines, and they cannot already have been seen by the event
    loop. (Otherwise it would be ambiguous whether they are inside the isolated region or not.) If `coro` depends on
    other coroutines, then `tinyio.copy` can be used to asychronously copy their results over.

    **Arguments:**

    - `coro`: a tinyio coroutine.
    - `exception_group`: as the `exception_group` parameter of [`tinyio.Loop.run`][].

    **Returns:**

    A 2-tuple:

    - the first element is either the result of `fn(*args)`, or its exception.
    - the second element is whether `fn(*args)` succeeded (`True`) or raised an exception (`False`).

    !!! Example

        Run a coroutine, and always perform cleanup even if an error was raised:
        ```python
        def get_request():
            conn = make_connection():
            out, success = yield tinyio.isolate(conn.say_hello())
            yield conn.say_goodbye()
            if success:
                return out
            else:
                raise out
        ```

    !!! Example

        If another coroutine provides an output that must be consumed by the isolated coroutine, then it cannot be
        yielded by the isolated coroutine.

        ```python
        # The following code is wrong!

        def main():
            get_x = return_x()
            # Schedule `get_x` outside of the isolated region...
            yield get_x
            yield tinyio.isolate(use_x(get_x))

        def return_x():
            yield
            return 3

        def use_x(get_x):
            # ...and also schedule `get_x` inside of the isolated region! This is not possible.
            x = yield get_x
        ```

        Instead, you need to make a fresh coroutine to use within the isolated region:

        ```python
        def main():
            get_x = return_x()
            yield get_x
            get_x_copy = yield tinyio.copy(get_x)  # this line is new
            yield tinyio.isolate(use_x(get_x_copy))
        ```
    """

    try:
        with Loop().runtime(coro, exception_group) as gen:
            while True:
                try:
                    wait = next(gen)
                except StopIteration as e:
                    return e.value, True
                if wait is None:
                    yield
                else:
                    yield run_in_thread(wait)
    # Catch all `Exception`s except `AssertionError`, which IMO should really be a `BaseException` – it usually
    # indicates a fatal exception because an expected invariant is not true.
    except AssertionError:
        raise
    # Do *not* catch `BaseException` here: in particular `tinyio.CancellationError` must be allowed to propagate; also
    # things like `SystemExit`/`KeyboardInterrupt` are probably best treated as fatal.
    except Exception as e:
        return e, False
