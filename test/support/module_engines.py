"""Initialization commands for the module engines used by integration tests."""

import os
from pathlib import Path
import shlex
import shutil

import pytest


def module_init(engine):
    if engine.startswith("lmod"):
        init = Path(os.environ.get("LMOD_INIT", "/usr/share/lmod/lmod/init/bash"))
        if not init.is_file():
            pytest.skip("Lmod is not installed")
        return f"source {shlex.quote(str(init))}"
    cmd = os.environ.get("MODULES_CMD") or shutil.which("modulecmd")
    if not cmd:
        for path in ("/usr/lib/x86_64-linux-gnu/modulecmd.tcl", "/usr/share/modules/libexec/modulecmd.tcl"):
            if Path(path).is_file():
                cmd = path
                break
    if not cmd:
        pytest.skip("Environment Modules is not installed; set MODULES_CMD")
    launcher = f"tclsh {shlex.quote(cmd)}" if cmd.endswith(".tcl") else shlex.quote(cmd)
    return f'eval "$({launcher} bash autoinit)"'

