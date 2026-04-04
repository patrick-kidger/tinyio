# Asynchronous iterators

You can use the following pattern to implement asynchronous iterators:

```python
def my_coro():
    for x in slow_range(5):
        x = yield x
        print(f"Got {x}")
```
where:
```python
def slow_range(x):  # this function is an iterator-of-coroutines
    for i in range(x):
        yield slow_range_i(i)  # this `yield` statement is seen by the `for` loop

def slow_range_i(i):  # this function is a coroutine
    yield tinyio.sleep(1)  # this `yield` statement is seen by the `tinyio.Loop()`
    return i
```

Here we just have `yield` being used in a couple of different ways that you're already used to:

- as a regular Python generator/iterator;
- as a `tinyio` coroutine.

For an example of this, see the source code for [`tinyio.as_completed`][].
