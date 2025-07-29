import tinyio


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
