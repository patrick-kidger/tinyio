# Threading

Blocking functions can be ran in threads using `tinyio.run_in_thread(fn, *args, **kwargs)`, which gives a coroutine you can `yield` on.

!!! Example

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

---

::: tinyio.run_in_thread

---

::: tinyio.ThreadPool
    options:
        members:
            - __init__
            - run_in_thread
            - map
