# Getting started

_Ever used `asyncio` and wished you hadn't?_

`tinyio` is a tiny (~400 lines) event loop for Python, born out of frustration with trying to get robust error handling with `asyncio`. ([link1](https://sailor.li/asyncio), [link2](https://lucumr.pocoo.org/2016/10/30/i-dont-understand-asyncio/).)

This is an alternative for the simple use-cases, where you just need an event loop, and want to crash the whole thing if anything goes wrong. (Raising an exception in every coroutine so it can clean up its resources.)

## Installation

```bash
pip install tinyio
```

Requires Python 3.11+.

## Quick example

```python
import tinyio

def slow_add_one(x: int):
    yield tinyio.sleep(1)
    return x + 1

def foo():
    four, five = yield [slow_add_one(3), slow_add_one(4)]
    return four, five

loop = tinyio.Loop()
out = loop.run(foo())
assert out == (4, 5)
```

## Next steps

Check out the [coroutines and loops](./api/loops.md) page.
