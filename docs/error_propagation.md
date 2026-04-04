# Error propagation

If any coroutine raises an error, then:

1. All coroutines across the entire loop will have [`tinyio.CancelledError`][] raised in them (from whatever `yield` point they are currently waiting at).
2. Any functions ran in threads via [`tinyio.run_in_thread`][] will also have [`tinyio.CancelledError`][] raised in the thread.
3. The original error is raised out of [`tinyio.Loop.run`][]. This behaviour can be configured (e.g. to collect errors into a `BaseExceptionGroup`) by setting `.run(..., exception_group=None/False/True)`.

This gives every coroutine a chance to shut down gracefully. Debuggers like [`patdb`](https://github.com/patrick-kidger/patdb) offer the ability to navigate across exceptions in an exception group, allowing you to inspect the state of all coroutines that were related to the error.

---

::: tinyio.CancelledError
    options:
        members: []
