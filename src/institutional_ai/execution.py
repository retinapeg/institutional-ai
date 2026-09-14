"""Bounded local execution. POSIX process groups include CLI descendants."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event


class ExecutionError(RuntimeError):
    pass


class Cancelled(ExecutionError):
    pass


class ExecutionTimeout(ExecutionError):
    pass


@dataclass
class RunControl:
    cancel: Event = field(default_factory=Event)
    seconds: float = 900
    started: float = field(default_factory=time.monotonic)

    def check(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("user cancelled the run")
        if time.monotonic() - self.started >= self.seconds:
            raise ExecutionTimeout("project wall-time limit exhausted")


CURRENT_RUN: ContextVar[RunControl | None] = ContextVar("institutional_run", default=None)


def run_subprocess(
    command: list[str],
    prompt: str = "",
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 90,
    output_limit: int = 1_048_576,
    grace: float = 2,
    control: RunControl | None = None,
) -> tuple[str, str]:
    """Drain both pipes without unbounded buffering; terminate the whole group on every exit."""
    control = control or CURRENT_RUN.get() or RunControl()
    control.check()
    started = time.monotonic()
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd, env=env, start_new_session=True,
    )
    assert process.stdin and process.stdout and process.stderr
    pending = memoryview(prompt.encode())
    output = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            os.set_blocking(process.stdin.fileno(), False)
            if pending:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            while selector.get_map() or process.poll() is None:
                control.check()
                if time.monotonic() - started >= timeout:
                    raise ExecutionTimeout(f"model call exceeded {timeout:g} seconds")
                for key, _ in selector.select(0.05):
                    if key.data == "stdin":
                        try:
                            count = os.write(key.fd, pending[:8192])
                            pending = pending[count:]
                        except BrokenPipeError:
                            pending = memoryview(b"")
                        if not pending:
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            buffer = output[key.data]
                            if len(buffer) + len(chunk) > output_limit:
                                raise ExecutionError(f"{key.data} exceeded {output_limit} bytes")
                            buffer.extend(chunk)
            if process.returncode != 0:
                # CLI stderr can contain prompts or credentials; do not persist it in the audit log.
                raise ExecutionError(f"CLI exited with code {process.returncode}; check CLI login/configuration")
            return output["stdout"].decode("utf-8"), output["stderr"].decode("utf-8")
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            until = time.monotonic() + grace
            while time.monotonic() < until:
                process.poll()
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
