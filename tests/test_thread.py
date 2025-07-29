import threading
import time
import warnings

import pytest
import tinyio


def test_in_thread():
    def _blocking_slow_add_one(x: int) -> int:
        time.sleep(0.1)
        return x + 1

    def _big_gather(x: int):
        out = yield [tinyio.run_in_thread(_blocking_slow_add_one, x) for _ in range(100)]
        return out

    loop = tinyio.Loop()
    start = time.time()
    out = loop.run(_big_gather(1))
    end = time.time()
    assert out == [2 for _ in range(100)]
    assert end - start < 0.5


@pytest.mark.parametrize("with_map", (False, True))
def test_thread_pool(with_map: bool):
    counter = 0
    lock = threading.Lock()

    def _count(x, y):
        nonlocal counter
        with lock:
            counter += 1
        time.sleep(0.01)
        assert counter <= 2
        with lock:
            counter -= 1
        return x, y

    def _run(max_threads):
        pool = tinyio.ThreadPool(max_threads)
        if with_map:
            out = yield pool.map(lambda i: _count(i, y=i), range(50))
        else:
            out = yield [pool.run_in_thread(_count, i, y=i) for i in range(50)]
        return out

    loop = tinyio.Loop()
    assert loop.run(_run(2)) == [(i, i) for i in range(50)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(BaseExceptionGroup):
            loop.run(_run(3))
