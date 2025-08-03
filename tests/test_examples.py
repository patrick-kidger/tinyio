import random
import time

import tinyio


def test_dataloading():
    outs = []

    def slow_transform(x):
        time.sleep(random.uniform(0.01, 0.02))  # slow I/O bound work
        return x * x

    def main():
        iterator = range(100)
        pool = tinyio.ThreadPool(16)
        async_iterator = yield tinyio.as_completed({pool.run_in_thread(slow_transform, item) for item in iterator})
        while not async_iterator.done():
            out = yield async_iterator.get()
            outs.append(out)
        return outs

    loop = tinyio.Loop()
    out = loop.run(main())
    assert set(out) == {x**2 for x in range(100)}
    assert out != [x**2 for x in range(100)]  # test not in order. Very low chance of failing, should be fine!
