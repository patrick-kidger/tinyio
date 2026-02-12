import tinyio


def _sleep(x):
    yield tinyio.sleep(x)
    return x


def test_as_completed():
    def _run():
        iterator = yield tinyio.as_completed({_sleep(0.3), _sleep(0.1), _sleep(0.2)})
        outs = []
        for x in iterator:
            x = yield x
            outs.append(x)
        return outs

    loop = tinyio.Loop()
    assert loop.run(_run()) == [0.1, 0.2, 0.3]


def test_as_completed_out_of_order():
    def _run():
        iterator = yield tinyio.as_completed({_sleep(0.3), _sleep(0.1), _sleep(0.2)})
        get1, get2, get3 = list(iterator)
        out3 = yield get3
        out2 = yield get2
        out1 = yield get1
        return [out1, out2, out3]

    loop = tinyio.Loop()
    assert loop.run(_run()) == [0.1, 0.2, 0.3]
