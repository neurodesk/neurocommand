"""Validate module engines promised by CI before collecting their tests."""

import os
import subprocess

import pytest

from test.support.module_engines import module_init


def pytest_sessionstart(session):
    engine = os.environ.get("NEUROCOMMAND_TEST_ENGINE")
    if not engine:
        return
    if engine not in {"modules", "lmod"}:
        raise pytest.UsageError(f"Unknown NEUROCOMMAND_TEST_ENGINE: {engine}")
    try:
        init = module_init(engine)
    except pytest.skip.Exception as exc:
        raise pytest.UsageError(f"Required module engine {engine} is unavailable: {exc}") from exc
    result = subprocess.run(
        ["bash", "-c", f"{init}\nmodule --version"],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise pytest.UsageError(
            f"Required module engine {engine} failed initialization:\n"
            f"{result.stdout}{result.stderr}"
        )
    if engine == "lmod" and not os.environ.get("LMOD_CMD"):
        raise pytest.UsageError("Required Lmod engine has no LMOD_CMD; source its bash initialization")
