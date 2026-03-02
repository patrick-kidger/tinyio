import asyncio

import pytest
import tinyio


def test_asyncio_inside_tinyio_basic():
    async def add_one(x):
        await asyncio.sleep(0.00001)
        return x + 1

    async def f(x):
        y = await add_one(x)
        z = await add_one(y)
        return z

    out = tinyio.Loop().run(tinyio.from_asyncio(f(3)))
    assert out == 5


def test_asyncio_inside_tinyio_complex():
    async def add_one(x):
        await asyncio.sleep(0.00001)
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
        ff = tinyio.from_asyncio(f(3))
        a, b = yield [ff, g(x)]
        a2 = yield ff
        return a + a2 + b

    out = tinyio.Loop().run(h(3))
    assert out == 23


def test_tinyio_inside_asyncio():
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
        out = await tinyio.to_asyncio(_diamond1(2))
        return out

    assert asyncio.run(f()) == 9


def test_other_asyncio_can_run():
    event = tinyio.Event()

    def _add_one(x: int) -> tinyio.Coro[int]:
        yield event.wait()
        return x + 1

    async def g():
        for _ in range(20):
            await asyncio.sleep(0)
        event.set()

    async def f():
        await asyncio.gather(g(), tinyio.to_asyncio(_add_one(1)))
        return 5

    assert asyncio.run(f()) == 5


# Error propagation tests


class _TestError(Exception):
    pass


def test_asyncio_inside_tinyio_error_in_nested():
    event = asyncio.Event()

    def background():
        yield
        event.set()
        while True:
            yield

    async def failing_asyncio():
        await asyncio.sleep(0.00001)
        await event.wait()
        raise _TestError("asyncio error")

    def main() -> tinyio.Coro[None]:
        yield {background()}
        yield tinyio.from_asyncio(failing_asyncio())

    with pytest.raises(_TestError, match="asyncio error"):
        tinyio.Loop().run(main())


def test_asyncio_inside_tinyio_error_in_main():
    asyncio_done = False
    event = tinyio.Event()

    async def slow_asyncio():
        nonlocal asyncio_done
        try:
            event.set()
            while True:
                await asyncio.sleep(0.001)
        except BaseException:
            asyncio_done = True
            raise

    def failing_tinyio() -> tinyio.Coro[None]:
        yield event.wait()
        raise _TestError("tinyio error")

    def main() -> tinyio.Coro[None]:
        yield [tinyio.from_asyncio(slow_asyncio()), failing_tinyio()]

    with pytest.raises(_TestError, match="tinyio error"):
        tinyio.Loop().run(main())
    assert asyncio_done


def test_tinyio_inside_asyncio_error_in_nested():
    def failing_tinyio() -> tinyio.Coro[None]:
        for _ in range(5):
            yield
        raise _TestError("tinyio error")

    async def main():
        await tinyio.to_asyncio(failing_tinyio())

    with pytest.raises(_TestError, match="tinyio error"):
        asyncio.run(main())


def test_tinyio_inside_asyncio_error_in_main():
    tinyio_done = False
    event = asyncio.Event()

    def slow_tinyio():
        nonlocal tinyio_done
        try:
            event.set()
            while True:
                yield tinyio.sleep(0.001)
        except BaseException:
            tinyio_done = True
            raise

    async def run_tinyio():
        await tinyio.to_asyncio(slow_tinyio())

    async def failing_asyncio():
        await asyncio.sleep(0.001)
        await event.wait()
        raise _TestError("asyncio error")

    async def main():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(run_tinyio())
            tg.create_task(failing_asyncio())

    with pytest.raises(ExceptionGroup) as catcher:
        asyncio.run(main())
    [test_error] = catcher.value.exceptions
    assert type(test_error) is _TestError
    assert str(test_error) == "asyncio error"
    assert tinyio_done
