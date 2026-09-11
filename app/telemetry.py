"""Request-local content-free timings, including nested native operations."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter

_sink: ContextVar[Callable[[str, float], None] | None] = ContextVar("timing_sink", default=None)


@contextmanager
def timing_scope(sink: Callable[[str, float], None]) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def measured_scope(stage: str) -> Iterator[None]:
    """Measure one dynamically named stage inside the active request scope."""

    start = perf_counter()
    try:
        yield
    finally:
        sink = _sink.get()
        if sink is not None:
            sink(stage, perf_counter() - start)


def measured[**P, R](stage: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def run(*args: P.args, **kwargs: P.kwargs) -> R:
            start = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                sink = _sink.get()
                if sink is not None:
                    sink(stage, perf_counter() - start)

        return run

    return decorate
