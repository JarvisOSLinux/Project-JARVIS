"""Await blocking work without holding the process open at exit."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable


async def run_in_daemon_thread(fn: Callable[..., Any], *args: Any) -> Any:
    """Run fn in a daemon thread and await its result.

    Not asyncio.to_thread or run_in_executor(None, ...): the default
    executor's threads are joined when the process exits, so quitting waited
    for whatever they were blocked on -- a model reply in flight, or the
    user's next line at the `jarvis chat` prompt, which never came. A daemon
    thread is simply dropped at exit.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def deliver(setter: Callable[[Any], None], value: Any) -> None:
        if not future.done():
            setter(value)

    def work() -> None:
        try:
            result = fn(*args)
        except BaseException as e:  # handed to the awaiting task
            loop.call_soon_threadsafe(deliver, future.set_exception, e)
        else:
            loop.call_soon_threadsafe(deliver, future.set_result, result)

    name = f"jarvis-{getattr(fn, '__name__', 'work')}"
    threading.Thread(target=work, name=name, daemon=True).start()
    return await future
