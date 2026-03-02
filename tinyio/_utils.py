import contextlib
import select
import socket
import threading
import types


# Not sure if this lock is really necessary, but it's easier to reason about this way.
_global_event_lock = threading.Lock()


class EventWithFileno:
    """Like `threading.Event`, but has a fileno and can thus be used across processes."""

    def __init__(self):
        # socketpair works with select on all platforms (including Windows)
        self._read_sock, self._write_sock = socket.socketpair()
        self._read_sock.setblocking(False)
        self._write_sock.setblocking(False)

    def set(self):
        with _global_event_lock:
            with contextlib.suppress(OSError):
                # Can be a `BlockingIOError` if this is already set.
                # Can be a general `OSError` if the we have already `.close`d.
                self._write_sock.send(b"\x00")

    def clear(self):
        with _global_event_lock:
            with contextlib.suppress(OSError):
                while len(self._read_sock.recv(1024)) > 0:
                    pass

    def wait(self, timeout: None | int | float = None):
        if timeout is None or timeout > 0:
            with contextlib.suppress(ValueError):
                # ValueError if we have already `.close`d, as then the fileno is -1.
                select.select([self._read_sock], [], [], timeout)
        # Don't consume the bytes here - let clear() do that

    def close(self):
        with _global_event_lock:
            self._read_sock.close()
            self._write_sock.close()

    def get_write_fd(self):
        return self._write_sock.fileno()


class SimpleContextManager:
    def __init__(self, enter, exit):
        self.enter = enter
        self.exit = exit

    def __enter__(self):
        return self.enter

    def __exit__(self, exc_type, exc_value, exc_tb):
        __tracebackhide__ = True
        self.exit(exc_value)


def filter_traceback(e: BaseException) -> None:
    pieces = []
    tb = e.__traceback__
    while tb is not None:
        if not tb.tb_frame.f_locals.get("__tracebackhide__", False):
            pieces.append((tb.tb_frame, tb.tb_lasti, tb.tb_lineno))
        tb = tb.tb_next
    tb = None
    for piece in reversed(pieces):
        tb = types.TracebackType(tb, *piece)
    e.with_traceback(tb)
