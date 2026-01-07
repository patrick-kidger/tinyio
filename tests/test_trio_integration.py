import tinyio
import trio  # pyright: ignore[reportMissingImports]


def test_trio_inside_tinyio_basic():
    async def add_one(x):
        await trio.sleep(0.00001)
        return x + 1

    async def f(x):
        y = await add_one(x)
        z = await add_one(y)
        return z

    out = tinyio.Loop().run(tinyio.from_trio(f, 3))
    assert out == 5


def test_trio_inside_tinyio_complex():
    async def add_one(x):
        await trio.sleep(0.00001)
        return x + 1

    async def f(x):
        y = await add_one(x)
        z = await add_one(y)
        return z

    def g(x):
        for _ in range(20):
            yield
        return x + 10

    def h(x):
        ff = tinyio.from_trio(f, 3)
        a, b = yield [ff, g(x)]
        a2 = yield ff
        return a + a2 + b

    out = tinyio.Loop().run(h(3))
    assert out == 23


def test_tinyio_inside_trio():
    def _add_one(x: int) -> tinyio.Coro[int]:
        yield tinyio.sleep(0.1)
        return x + 1

    def _diamond1(x: int) -> tinyio.Coro[int]:
        y = _add_one(x)
        a, b = yield [_diamond2(y, 1), _diamond2(y, 2)]
        return a + b

    def _diamond2(y: tinyio.Coro[int], factor: int):
        z = yield y
        return z * factor

    async def f():
        out = await tinyio.to_trio(_diamond1(2))
        return out

    assert trio.run(f) == 9
