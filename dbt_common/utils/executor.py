import concurrent.futures
from contextlib import contextmanager
from typing import Protocol, Optional

from dbt_common.context import (
    get_invocation_context,
    reliably_get_invocation_var,
    InvocationContext,
    set_otel_context,
)

from opentelemetry.context.context import Context
from opentelemetry import context as opentelemetry_context


class ConnectingExecutor(concurrent.futures.Executor):
    def submit_connected(self, adapter, conn_name, func, *args, **kwargs):
        def connected(conn_name, func, *args, **kwargs):
            with self.connection_named(adapter, conn_name):
                return func(*args, **kwargs)

        return self.submit(connected, conn_name, func, *args, **kwargs)


# a little concurrent.futures.Executor for single-threaded mode
class SingleThreadedExecutor(ConnectingExecutor):
    def submit(*args, **kwargs):
        # this basic pattern comes from concurrent.futures.Executor itself,
        # but without handling the `fn=` form.
        if len(args) >= 2:
            self, fn, *args = args
        elif not args:
            raise TypeError(
                "descriptor 'submit' of 'SingleThreadedExecutor' object needs an argument"
            )
        else:
            raise TypeError(
                "submit expected at least 1 positional argument, got %d" % (len(args) - 1)
            )
        fut = concurrent.futures.Future()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            fut.set_exception(exc)
        else:
            fut.set_result(result)
        return fut

    @contextmanager
    def connection_named(self, adapter, name):
        yield


class MultiThreadedExecutor(
    ConnectingExecutor,
    concurrent.futures.ThreadPoolExecutor,
):
    @contextmanager
    def connection_named(self, adapter, name):
        with adapter.connection_named(name):
            yield


class ThreadedArgs(Protocol):
    single_threaded: bool


class HasThreadingConfig(Protocol):
    args: ThreadedArgs
    threads: Optional[int]


def _thread_initializer(invocation_context: InvocationContext, context: Context) -> None:
    invocation_var = reliably_get_invocation_var()
    invocation_var.set(invocation_context)
    set_otel_context(context)


def executor(config: HasThreadingConfig) -> ConnectingExecutor:
    if config.args.single_threaded:
        return SingleThreadedExecutor()
    else:
        return MultiThreadedExecutor(
            max_workers=config.threads,
            initializer=_thread_initializer,  # type: ignore
            initargs=(get_invocation_context(), opentelemetry_context.get_current()),  # type: ignore
        )
