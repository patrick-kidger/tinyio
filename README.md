<h1 align="center">tinyio</h1>
<h2 align="center">A tiny (~400 lines) event loop for Python</h2>

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

- Somewhat unusually, our syntax uses `yield` rather than `await`, but the behaviour is the same. Await another coroutine with `yield coro`. Await on multiple with `yield [coro1, coro2, ...]` (a 'gather' in `asyncio` terminology; a 'nursery' in `trio` terminology).
- An error in one coroutine will cancel all coroutines across the entire event loop.
    - If the erroring coroutine is sequentially depended on by a chain of other coroutines, then we chain their tracebacks for easier debugging.
    - Errors propagate to and from synchronous operations ran in threads.
- Can nest tinyio loops inside each other, none of this one-per-thread business.
- Ludicrously simple. No need for futures, tasks, etc. Here's the entirety of the day-to-day API:
    ```python
    tinyio.Loop
    tinyio.run_in_thread
    tinyio.sleep
    tinyio.CancelledError
    ```

## Installation

```
pip install tinyio
```

## Documentation

Available at [https://docs.kidger.site/tinyio](https://docs.kidger.site/tinyio).

## FAQ

<details>
<summary>Why <code>yield</code> — why not <code>await</code> like is normally seen for coroutines?</summary>
<br>

The reason is that `await` does not offer a suspension point to an event loop (it just calls `__await__` and maybe *that* offers a suspension point), so if we wanted to use that syntax then we'd need to replace `yield coro` with something like `await tinyio.Task(coro)`. The traditional syntax is not worth the extra class.
</details>

<details>
<summary>I have a function I want to be a coroutine, but it has zero <code>yield</code> statements, so it is just a normal function?</summary>
<br>

You can distinguish it from a normal Python function by putting `if False: yield` somewhere inside its body. Another common trick is to put a `yield` statement after the final `return` statement. Bit ugly but oh well.
</details>

<details>
<summary>vs <code>asyncio</code> or <code>trio</code>?</summary>
<br>

I wasted a *lot* of time trying to get correct error propagation with `asyncio`, trying to reason whether my tasks would be cleaned up correctly or not (edge-triggered vs level-triggered etc etc). `trio` is excellent but still has a one-loop-per-thread rule, and doesn't propagate cancellations to/from threads. These points inspired me to try writing my own.

`tinyio` has the following unique features, and as such may be the right choice if any of the following are must-haves for you:

- the propagation of errors to/from threads;
- no one-loop-per-thread rule;
- simple+robust error semantics (crash the whole loop if anything goes wrong);
- tiny, hackable, codebase.

Conversely, at least right now we don't ship batteries-included with a few things:

- asynchronously launching subprocesses / making network requests / accessing the file system (in all cases use `run_in_thread` instead);
- scheduling work on the event loop whilst cleaning up from errors.

If none of the bullet points are must-haves for you, or if any of its limitations are dealbreakers, then either `trio` or `asyncio` are likely to be better choices. :)

</details>
