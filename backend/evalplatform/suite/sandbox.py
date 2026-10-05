"""Run model-generated Python against unit tests in a resource-limited subprocess.

This is a *best-effort* sandbox for evaluation harness use (isolated temp dir, scrubbed env,
CPU/memory/file limits, wall-clock timeout, static blocklist for obviously dangerous calls).
It is not a security boundary — for untrusted code at scale run the harness inside a container
or a Modal Sandbox.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field

BLOCKLIST = [
    r"\bimport\s+(subprocess|socket|ctypes|shutil|multiprocessing|urllib|requests|http|ftplib|smtplib|pty)\b",
    r"\bfrom\s+(subprocess|socket|ctypes|shutil|multiprocessing|urllib|requests|http|ftplib|smtplib|pty)\b",
    r"\bos\.(system|popen|remove|unlink|rmdir|removedirs|kill|fork|exec\w*|spawn\w*)\b",
    r"\b__import__\b",
    r"\bopen\s*\([^)]*['\"][wax+]",
]

_RUNNER = r'''
import json, sys, traceback
ns = {"__name__": "solution"}
results = []
code = open("solution.py", encoding="utf-8").read()
tests = json.load(open("tests.json", encoding="utf-8"))
load_error = None
try:
    exec(compile(code, "solution.py", "exec"), ns)
except BaseException as e:
    load_error = f"{type(e).__name__}: {e}"[:300]
for name, snippet in tests:
    if load_error:
        results.append([name, False, load_error]); continue
    try:
        exec(compile(snippet, name, "exec"), dict(ns))
        results.append([name, True, ""])
    except BaseException as e:
        results.append([name, False, f"{type(e).__name__}: {e}"[:300]])
sys.stdout.write("\n__RESULTS__" + json.dumps(results))
'''


@dataclass
class SandboxResult:
    results: list[tuple[str, bool, str]] = field(default_factory=list)
    error: str | None = None
    timed_out: bool = False

    @property
    def passed(self) -> int:
        return sum(1 for _, ok, _ in self.results if ok)


def _limits():  # pragma: no cover - runs in the child process
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


async def run_tests(code: str, tests: list[tuple[str, str]], timeout: float = 15.0) -> SandboxResult:
    for pat in BLOCKLIST:
        m = re.search(pat, code)
        if m:
            return SandboxResult(error=f"Blocked unsafe construct in generated code: {m.group(0)!r}")
    with tempfile.TemporaryDirectory(prefix="evalsbx_") as d:
        with open(os.path.join(d, "solution.py"), "w", encoding="utf-8") as f:
            f.write(code)
        with open(os.path.join(d, "tests.json"), "w", encoding="utf-8") as f:
            json.dump(tests, f)
        with open(os.path.join(d, "runner.py"), "w", encoding="utf-8") as f:
            f.write(_RUNNER)
        env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8", "HOME": d}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "runner.py", cwd=d, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_limits if os.name == "posix" else None,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SandboxResult(error=f"Timed out after {timeout:.0f}s (infinite loop or very slow solution).", timed_out=True)
    text = out.decode("utf-8", "replace")
    marker = text.rfind("__RESULTS__")
    if marker < 0:
        tail = (err.decode("utf-8", "replace") or text)[-300:]
        return SandboxResult(error=f"Program crashed before reporting results: {tail.strip()}")
    try:
        parsed = json.loads(text[marker + len("__RESULTS__"):])
    except json.JSONDecodeError:
        return SandboxResult(error="Could not parse test results.")
    return SandboxResult(results=[(n, bool(ok), d) for n, ok, d in parsed])
