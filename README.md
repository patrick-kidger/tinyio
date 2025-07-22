<h1 align="center">tinyio</h1>
<h2 align="center">A tiny (~200 lines) event loop for Python</h2>

_Ever used `asyncio` and wished you hadn't?_

`tinyio` is a dead-simple event loop for Python, born out of my frustration with trying to get robust error handling with `asyncio`. (I'm not the only one running into its sharp corners: [link1](https://sailor.li/asyncio), [link2](https://lucumr.pocoo.org/2016/10/30/i-dont-understand-asyncio/).)

This is an alternative for the simple use-cases, where you just need an event loop, and want to crash the whole thing if anything goes wrong. (Raising an exception in every coroutine so it can clean up its resources.)

```python
import tinyio

def slow_add_one(x: int):
    yield tinyio.sleep(1)
    return x + 1

def foo():
    four, five = yield [slow_add_one(3), slow_add_one(4)]
    return four, five

loop = tinyio.Loop()
out = loop.run(foo())
assert out == (4, 5)
```

- Somewhat unusually, our syntax uses `yield` rather than `await`, but the behaviour is the same. Await another coroutine with `yield coro`. Await on multiple with `yield [coro1, coro2, ...]` (a 'gather' in asyncio terminology; a 'nursery' in trio terminology).
- An error in one coroutine will cancel all coroutines across the entire event loop.
    - If the erroring coroutine is sequentially depended on by a chain of other coroutines, then we chain their tracebacks for easier debugging.
    - Errors even propagate to and from synchronous operations ran in threads.
- Can nest tinyio loops inside each other, none of this one-per-thread business.
- Ludicrously simple. No need for futures, tasks, etc. Here's the full API:
    ```python
    tinyio.Loop
    tinyio.CancelledError
    tinyio.sleep
    tinyio.run_in_thread
    ```

## Installation

```
pip install tinyio
```

## Documentation

#### Loops

Create a loop with `tinyio.Loop()`. It has a single method, `.run(coro)`, which consumes a coroutine, and which returns the output of that coroutine.

#### Yielding

You can `yield` three possible things:

- `yield`: yield nothing, this just pauses and gives other coroutines a chance to run.
- `yield coro`: wait on a single coroutine, in which case we'll resume with the output of that coroutine once it is available.
- `yield [coro1, coro2, coro3]`: wait on multiple coroutines by putting them in a list, and resume with a list of outputs once all have completed. This is what asyncio calls a 'gather' or 'TaskGroup', and what trio calls a 'nursery'.

You can safely `yield` the same coroutine multiple times, e.g. perhaps four coroutines have a diamond dependency pattern, with two coroutines each depending on a single shared one.

#### Threading

Synchronous functions can be ran in threads using `tinyio.run_in_thread`, which returns a coroutine you can `yield` on:
```python
import time, tinyio

def slow_blocking_add_one(x: int) -> int:
    time.sleep(1)
    return x + 1

def foo(x: int):
    out = yield [tinyio.run_in_thread(slow_blocking_add_one, x) for _ in range(3)]
    return out

loop = tinyio.Loop()
out = loop.run(foo(x=1))  # runs in one second, not three
assert out == [2, 2, 2]
```

#### Error propagation

If any coroutine raises an error, then:

1. All coroutines across the entire loop will have `tinyio.CancelledError` raised in them (from whatever `yield` point they are currently waiting at).
2. Any functions ran in threads via `tinyio.run_in_thread` will also have `tinyio.CancelledError` raised in the thread.
3. All errors are collected and raised in a `BaseExceptionGroup`. The original error will be the first in this group.

This gives every coroutine a chance to shut down gracefully. Debuggers like [`patdb`](https://github.com/patrick-kidger/patdb) offer the ability to navigate across exceptions in an exception group, allowing you to inspect the state of all coroutines that were related to the error.

## FAQ

<details>
<summary>Why <code>yield</code> -- why not <code>await</code> like is normally seen for coroutines?</summary>
<br>

The reason is that `await` does not offer a suspension point to an event loop (it just calls `__await__` and maybe *that* offers a suspension point), so if we wanted to use that syntax then we'd need to replace `yield coro` with something like `await tinyio.Task(coro)`. The traditional syntax is not worth the extra class.
</details>

<details>
<summary>I have a function I want to be a coroutine, but it has zero <code>yield</code> statements, so it is just a normal function?</summary>
<br>

You can distinguish it from a normal Python function by putting `if False: yield` somewhere inside its body. Another common trick is to put a `yield` statement after the final `return` statement. Bit ugly but oh well.
</details>

<details>
<summary>Any funny business to know around loops?</summary>
<br>

The output of each coroutine is stored on the `Loop()` class. If you attempt to run a previously-ran coroutine in a new `Loop()` then they will be treated as just returning `None`, which is probably not what you want.
</details>

<details>
<summary>vs <code>asyncio</code> or <code>trio</code>?.</summary>
<br>

I wasted a *lot* of time trying to get correct error propagation with `asyncio`, trying to reason whether my tasks would be cleaned up correctly or not (edge-triggered vs level-triggered etc etc). `trio` is excellent but still has a one-loop-per-thread rule, and doesn't propagate cancellations to/from threads. Thede points inspired me to try writing my own.

Nonetheless you'll definitely still want one of the above if you need anything fancy. If you don't, and you really really want simple error semantics, then maybe `tinyio` is for you instead. (In particular `trio` will be a better choice if you still need the event loop when cleaning up from errors; in contrast `tinyio` does not allow scheduling work back on the event loop at that time.)
</details>
