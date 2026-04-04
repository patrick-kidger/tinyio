# Coroutines and loops

Create a loop with `tinyio.Loop()`. It has a single method, `.run(coro)`, which consumes a coroutine, and which returns the output of that coroutine.

Each coroutine is just a Python generator using `yield`. Coroutines can `yield` four possible things:

- `yield`: yield nothing, this just pauses and gives other coroutines a chance to run.
- `yield coro`: wait on a single coroutine, in which case we'll resume with the output of that coroutine once it is available.
- `yield [coro1, coro2, coro3]`: wait on multiple coroutines by putting them in a list, and resume with a list of outputs once all have completed. This is what `asyncio` calls a 'gather' or 'TaskGroup', and what `trio` calls a 'nursery'.
- `yield {coro1, coro2, coro3}`: schedule one or more coroutines but do not wait on their result — they will run independently in the background.

If you `yield` on the same coroutine multiple times (e.g. in a diamond dependency pattern) then the coroutine will be scheduled once, and on completion all dependees will receive its output. (You can even do this if the coroutine has already finished: `yield` on it to retrieve its output.)

---

::: tinyio.Loop
    options:
        members:
            - __init__
            - run
