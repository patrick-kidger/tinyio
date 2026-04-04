# Isolation

If you would like to isolate a particular coroutine and allow it to crash *without* also crashing the entire event loop, then you can use `tinyio.isolate`.

This will nest another [`tinyio.Loop`][] inside of your existing loop. This means that it crashing will only affect everything in that nested loop – i.e. crash the provided coroutines and all coroutines that it yields — and then return the result or the error.

---

::: tinyio.isolate

---

::: tinyio.copy
