import time

import tinyio


def test_sleep():
    outs = []

    def f():
        start = time.monotonic()
        yield [tinyio.sleep(0.05), tinyio.sleep(0.1)]
        actual_duration = time.monotonic() - start
        # Note that these are pretty inaccurate tolerances! This is about what we get with `asyncio` too.
        # The reason for this seems to be the accuracy in the `threading.Event.wait()` that we bottom out in. If we need
        # greater resolution than this then we could do that by using a busy-loop for the last 1e-2 seconds.
        success = 0.09 < actual_duration < 0.11
        outs.append(success)

    loop = tinyio.Loop()
    for _ in range(5):
        loop.run(f())
    assert sum(outs) >= 4  # We allow one failure, to decrease flakiness.


def _sleep(x):
    yield tinyio.sleep(x)
    return 3


def _test_timeout():
    out1, success1 = yield tinyio.timeout(_sleep(0.2), 0.3)
    out2, success2 = yield tinyio.timeout(_sleep(0.2), 0.1)
    assert out1 == 3
    assert out2 is None
    assert success1 is True
    assert success2 is False


def test_timeout():
    loop = tinyio.Loop()
    loop.run(_test_timeout())
