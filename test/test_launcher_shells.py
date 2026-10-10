from pathlib import Path
import json
import os
import pty
import re
import select
import shlex
import subprocess
import time

import pytest

from test.support.artifacts import refresh
from test.support.bundles import run_engine
from test.support.launchers import launcher_tree
from test.support.module_engines import module_init
from test.support.shell import clean_env

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua'])
def test_named_and_isolated_launcher_use_explicit_image_and_module_env(tmp_path, engine):
    install, root, runtime = launcher_tree(tmp_path, engine)
    first = install / 'containers/first_1.0_20260629'
    (first / 'manual_module_files/tcl').mkdir(parents=True, exist_ok=True)
    (first / 'manual_module_files/first').write_text('prepend_path(\"PATH\", \"/unrelated/bin\")\n')
    (first / 'manual_module_files/tcl/first').write_text('prepend-path PATH /unrelated/bin\n')
    assert refresh(first, 'first_1.0_20260629.simg').returncode == 0
    init = module_init(engine)
    # bundle_tree's declaration uses demo-bundle/1.0; match the manifest.
    script = f'''set -e
{init}
module use {shlex.quote(str(root))}
export PATH=/unrelated/bin:$PATH
bash {shlex.quote(str(install / 'fetch_and_run.sh'))} demo-bundle 1.0 first 'argument with spaces' '$literal'
bash {shlex.quote(str(install / 'fetch_and_run.sh'))} first 1.0 --container-shell
'''
    if engine.startswith('lmod'):
        run_engine(engine, root, 'true\n')
    result = subprocess.run(['bash', '-c', script], env=clean_env(NEURODESKTOP_LOCAL_CONTAINERS=str(install / 'containers'), NEURODESK_CONTAINER_RUNTIME=str(runtime), CVMFS_DISABLE='true'), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    named, isolated = [json.loads(line) for line in result.stdout.splitlines()]
    assert named['argv'][-4:] == [str(install / 'containers/first_1.0_20260629/first_1.0_20260629.simg'), 'first', 'argument with spaces', '$literal']
    assert '/configured-bind' in named['argv']
    assert isolated['argv'][1] == 'shell'
    assert isolated['argv'][-1].endswith('/first_1.0_20260629.simg')


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua'])
@pytest.mark.parametrize('preload', [False, True])
def test_host_shell_initializes_once_and_preserves_native_ownership(tmp_path, engine, preload):
    install, root, runtime = launcher_tree(tmp_path, engine)
    if engine.startswith('lmod'): run_engine(engine, root, 'true\n')
    home = tmp_path / 'home'
    home.mkdir()
    marker = tmp_path / 'startup-count'
    unrelated = tmp_path / 'startup-bin'
    unrelated.mkdir()
    init = module_init(engine)
    (home / '.bashrc').write_text(f'''PS1='$ '
export PATH={shlex.quote(str(unrelated))}:/usr/bin:/bin
printf 'startup\\n' >> {shlex.quote(str(marker))}
{init}
''')
    prefix = f'{init}\nmodule use {shlex.quote(str(root))}\n'
    if preload: prefix += 'module load first/1.0\n'
    command = prefix + f'exec bash {shlex.quote(str(install / "fetch_and_run.sh"))} demo-bundle 1.0 --host-shell'
    env = clean_env(HOME=str(home), TERM='dumb', NEURODESKTOP_LOCAL_CONTAINERS=str(install / 'containers'), NEURODESK_CONTAINER_RUNTIME=str(runtime), CVMFS_DISABLE='true')
    master, slave = pty.openpty()
    process = subprocess.Popen(['bash', '-c', command], env=env, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    output = b''
    sent = False
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try: chunk = os.read(master, 65536)
                except OSError: break
                if not chunk: break
                output += chunk
            if not sent and b'[demo-bundle/1.0]' in output:
                os.write(master, b'command -v first\nMODULES_PAGER=cat ml third/1.0\ncommand -v third\nMODULES_PAGER=cat module unload demo-bundle/1.0\nif command -v first >/dev/null; then echo FIRST_RETAINED; else echo FIRST_REMOVED; fi\nif command -v second >/dev/null; then echo SECOND_RETAINED; else echo SECOND_REMOVED; fi\ncase "$PATH" in *startup-bin*) echo STARTUP_PATH_KEPT;; esac\nexit\n')
                sent = True
            if process.poll() is not None and not ready: break
        if process.poll() is None: process.kill()
        process.wait(timeout=5)
    finally:
        os.close(master)
    transcript = output.decode(errors='replace')
    assert sent and process.returncode == 0, transcript
    assert '/first_1.0_20260629/first' in transcript
    assert '/third_1.0_20260629/third' in transcript
    # Echoed input contains both labels; require the executed result on its own line.
    normalized = transcript.replace('\r', '')
    normalized = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', normalized)
    assert '\n' + ('FIRST_RETAINED' if preload else 'FIRST_REMOVED') + '\n' in normalized
    assert '\nSECOND_REMOVED\n' in normalized
    assert '\nSTARTUP_PATH_KEPT\n' in normalized
    assert marker.read_text() == 'startup\n'
    assert 'unexpected-fetch' not in transcript


def test_bundle_isolated_shell_and_bad_mode_fail_before_fetch(tmp_path):
    install, root, runtime = launcher_tree(tmp_path, 'modules')
    for mode in ['--container-shell', '20260629']:
        result = subprocess.run(['bash', str(install / 'fetch_and_run.sh'), 'demo-bundle', '1.0', mode], env=clean_env(NEURODESKTOP_LOCAL_CONTAINERS=str(install / 'containers')), capture_output=True, text=True)
        assert result.returncode == 2
        assert 'bundle has no container image' in result.stderr
        assert 'unexpected-fetch' not in result.stderr
    result = subprocess.run(['bash', str(install / 'fetch_and_run.sh'), 'demo-bundle', '1.0'], env=clean_env(NEURODESK_SHELL_MODE='invalid'), capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Invalid NEURODESK_SHELL_MODE' in result.stderr
