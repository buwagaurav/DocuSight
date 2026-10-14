"""Single owner thread for accelerator (MPS/CUDA) work.

PyTorch's MPS backend is not safe to drive from several threads at once —
concurrent use from the ingest pool, the ColPali indexer and request handlers
can deadlock or segfault. Every model call that touches the GPU is funnelled
through this one thread, which also keeps the model loaded exactly once.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device")
_local = threading.local()


def _mark_and_call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    _local.on_device = True
    return fn(*args, **kwargs)


def on_device(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run fn on the device thread and wait for the result (re-entrant)."""
    if getattr(_local, "on_device", False):
        return fn(*args, **kwargs)
    return _pool.submit(_mark_and_call, fn, *args, **kwargs).result()
