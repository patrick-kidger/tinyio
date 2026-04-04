# Asynchronous context managers

You can use the following pattern to implement context managers with asynchronous entry:

```python
def my_coro():
    with (yield my_context_manager(x=5)) as val:
        print(f"Got val {val}")
```
where:
```python
def my_context_manager(x):
    print("Initialising...")
    yield tinyio.sleep(1)
    print("Initialised")
    return make_context_manager(x)

@contextlib.contextmanager
def make_context_manager(x):
    try:
        yield x
    finally:
        print("Cleaning up")
```

This isn't anything fancier than just using a coroutine that returns a regular `with`-compatible context manager. See the source code for [`tinyio.Semaphore`][] for an example of this pattern.
