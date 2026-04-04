# FAQ

### Why `yield` — why not `await` like is normally seen for coroutines?

The reason is that `await` does not offer a suspension point to an event loop (it just calls `__await__` and maybe *that* offers a suspension point), so if we wanted to use that syntax then we'd need to replace `yield coro` with something like `await tinyio.Task(coro)`. The traditional syntax is not worth the extra class.

### I have a function I want to be a coroutine, but it has zero `yield` statements, so it is just a normal function?

You can distinguish it from a normal Python function by putting `if False: yield` somewhere inside its body. Another common trick is to put a `yield` statement after the final `return` statement. Bit ugly but oh well.

### vs `asyncio` or `trio`?

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
