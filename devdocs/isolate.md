# `tinyio.isolate`

I went back-and-forth on various designs for `tinyio.isolate`.

## Scheduling on the main loop vs scheduling on the isolated loop.

The key issue is around the coroutines spawned by the isolated coroutine (and any coroutines that they transitively spawn, and so on...). Are these part of the isolated loop or not? That is, if they crash, should they only crash the isolated loop, or the whole loop?

Note that we definitely need some means of placing things on the main loop (not just the isolated loop), as e.g. we may want an isolated coroutine to consume some input made available by the main loop... a dependency typically expressed as `yield some_coroutine`.

In terms of what this means under-the-hood:
- if they are on the isolated loop then they are just normal coroutines scheduled there;
- if they are on the main loop then we can still access their results from within the isolated loop by proxying their results over in a new coroutine; e.g. the following allows you to create a new coroutine that yields the results of the first:

    ```python
    # Usage: coro2 = yield copy(coro) whilst within the main loop. `coro2` is fresh and can be placed on a new loop.

    def copy(coro: tinyio.Coro[_T]) -> tinyio.Coro[tinyio.Coro[_T]]:
        pipe = []
        done = tinyio.Event()
        failed = tinyio.Event()

        def put_on_old_loop():
            try:
                out = yield coro
            except BaseException as e:
                pipe.append(e)
                failed.set()
                done.set()
                raise
            else:
                pipe.append(out)
                done.set()

        def put_on_new_loop() -> tinyio.Coro[_T]:
            yield done.wait()
            if failed.is_set():
                raise pipe[0]
            else:
                return pipe[0]

        yield {put_on_old_loop()}
        return put_on_new_loop()
    ```

After some thought, I realised that this question of placement cannot be determined automatically.

Here's a thought experiment:
```python
def foo():
    yield [bar, baz]

def main():
    yield [isolate(foo()), some_other_coro]
```
now let's suppose that `bar` crashes. Should `baz` also be crashed as well? If it is scheduled exclusively on the isolated loop, then yes. If it is scheduled on the main loop – or will be scheduled there in the future! – then no. And we have no way of telling which scenario we are in.

## Points to recall

1. a coroutine can be yielded in multiple places. Recall that `tinyio` allows you to yield previously-seen coroutines to get their result. In particular it is completely possible that the same coroutine will be yielded both inside and outside of the isolated region.

2. because we are in an asynchronous context, then we cannot rely on the order in which coroutines are yielded. For example supposing the isolated coroutine runs `yield coro`, then we might naively have a check of the form

    ```python
    if inspect.getgeneratorstate(coro) == inspect.GEN_CREATED:
        # brand-new coroutine, include it in the isolate
    else:
        # existing coroutine, keep it outside
    ```

    but in practice this just introduces a race condition; what if the coroutine is *going* to be yielded in the main loop later, but hasn't yet? We'd get different behaviour depending on the order in which things are scheduled.

In light of this, we might still imagine a design where fresh coroutines are stored on the isolated loop, until they appear on the main loop, at which point they transfer over (by some mechanism). This would also introduce an issue, however: what if some third coroutine within the isolated loop crashes? The order of that, relative to the transfer to the main loop, will determine whether the shared coroutine is also crashed. (Either not crashed on the main loop, or crashed within the isolated loop... which would then presumably leak out into crashing the main loop too, once it gets yielded there.) And in an async context, we can't make guarantees about the order in which things happen, so both are always possible.

# Rejected design 1

API of `tinyio.isolate(coro)`, intercept all coroutines yielded by `coro`, automatically detect whether that coroutine has started using `inspect.getgeneratorstate`, and pick the loop appropriately. This does not work for the reasons described above.

# Rejected design 2

API of `tinyio.isolate(coro)`, intercept all coroutines yielded by `coro`, and wrap all of them in a `tinyio.isolate` as well. (Plus unwrapping their results when sending.) This is essentially the maximalist answer to this choice by having the answer be 'never crash the other coroutines on the isolated loop, let them run to completion'.

We reject this because it does not fit `tinyio`'s crash-all-coroutines model – instead we're basically just back in the bad old days of `asyncio`'s edge-triggered exceptions. This is not easy to reason about.

# Rejected design 3

API of `tinyio.isolate(make_coro: Callable[..., tinyio.Coro], *args: tinyio.Coro)`, have `*args` be placed on the main loop, and assume/require that all coroutines directly yielded by `make_coro(*args)` are exclusively on the isolated loop.

This API is based around the observation that major use-case for (?) the isolated-on-main dependence is in consuming arguments from coroutines on the main loop.

This is 'fine', but suffers from (a) some API inconsistency with the rest of `tinyio` (which operates on coroutines directly, not functions-returning-coroutines), and (b) is not very explicit about the purpose of separating out those `*args`. A user may be surprised that `isolate(lambda: coro(x, y))` does not work equivalently to `isolate(coro, x, y)`.

# Rejected design 4

API of `tinyio.isolate(coro: tinyio.Coro, put_on_isolated_loop: Callable[[tinyio.Coro], bool])`, and call `put_on_isolated_loop(x)` on each `yield x` that occurs within the isolated region (directly from `coro` or transitively), and let a user explicitly decide.

This is also 'fine' but realistically almost all users are not going to be educated on the contents of this document, and may attempt to write out a rule (such as 'has the coroutine started') that are going to be flaky down the line.

# Accepted design

API of `tinyio.isolate(coro)`, and assume/require that all yielded coroutines from `coro` are exclusively on the isolated loop.

In order to consume the result of arguments from the main loop, then also publicly expose `tinyio.copy` for creating a fresh coroutine to place on the isolated loop:
```python
x = yield tinyio.copy(x)
out = yield tinyio.isolate(my_coro(x))
```

This suffers from the major issue that using this is easy to misuse: forgetting the `copy` will probably raise an error when `x` tries to be placed on both loops; placing the `copy` inside of `my_coro` will simply do nothing.

Relative to design 3, this does have the advantage of making it clear that `x` is being copied.

Still, this has probably the simplest semantics to explain to a user: it runs the provided coroutine in an isolated loop. If you want to bridge that isolation, use `tinyio.copy`.

I still don't love this, and would be open to other designs.
