We support `tinyio.Event.clear` (which trio does not have), and support timeouts in `tinyio.Event.wait` (which neither asyncio nor trio have). Why is this?

My original position was in-line with https://github.com/python-trio/trio/issues/637, in which it is argued that `Event.clear` is a misfeature that can be troublesome to reason about. Specifically, code immediately after `yield event.wait()` doesn't actually have any guarantee about the value of the event: the flag might have been toggled back-and-forth, which would trigger the `yield` i.e. schedule the coroutine, but with the flag set back to false by the time that the coroutine actually wakes up.

However, I knew that I wanted timeout support on `Event.wait`.
- This is because this makes it possible to implement `tinyio.sleep` without requiring a thread (to set the value of the event). Instead, we can just `yield Event().wait(timeout=delay)`.
- This also fits elegantly with our model of using a single `threading.Event.wait` to determine when to wake up our event loop and schedule work - we can implement sleeping by making it the timeout on that `threading.Event.wait` call.

This means we already do not have the guarantee that the event's flag has been set after the `yield`, as we may have timed out and moved on instead.

In light of that, I think it makes sense to fill out our API and support `Event.clear`. For the record: if you do need to ensure that the flag is set after `Event.wait`, even in the presence of other coroutines calling `.clear`, then you can use the following pattern:
```python
while True:
    yield event.wait()
    if event.is_set():
        break
```
It's not a strong feeling though, as the potential user-footgun-ness still remains.
