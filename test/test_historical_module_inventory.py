from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from artifact_renderer import legacy_module_content, managed_module_content, read_container_inventory
from test.support.cvmfs import make_container
from test.support.paths import MODULE_RECONCILIATION_SCRIPT as SCRIPT

@pytest.fixture
def historical_modules(tmp_path):
    current = 'demo_latest_20260629'
    old = 'demo_latest_latest'
    for name in (current, old):
        make_container(tmp_path, name, 'demo\n')
        (tmp_path / 'containers' / name / 'env.txt').write_text('')
    spec = read_container_inventory(tmp_path / 'containers' / current)
    old_spec = replace(spec, image_basename=old + '.simg')
    before = {}
    for format, filename in [('lua', 'latest.lua'), ('tcl', 'latest')]:
        content = legacy_module_content(
            old_spec, Path('/cvmfs/neurodesk.ardc.edu.au/containers') / old, format=format
        ).encode()
        for root in ['containers/modules', 'neurodesk-modules/data']:
            path = tmp_path / root / 'demo' / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            before[path] = content
    log = tmp_path / 'log.txt'
    log.write_text(f'{current} categories:data,\n')
    return spec, before, log


@pytest.mark.parametrize('format,filename', [('lua', 'latest.lua'), ('tcl', 'latest')])
@pytest.mark.parametrize('invalid', ['date', 'environment'])
def test_invalid_historical_inventory_refuses_ownership(tmp_path, historical_modules, format, filename, invalid):
    spec, before, _ = historical_modules
    content = before[tmp_path / 'containers/modules/demo' / filename].decode()
    if invalid == 'environment':
        old = tmp_path / 'containers/demo_latest_latest'
        old.rename(old.with_name('demo_latest_20250101'))
        content = content.replace('demo_latest_latest', 'demo_latest_20250101')
        (old.with_name('demo_latest_20250101') / 'env.txt').write_text('DEPLOY_ENV_BAD-NAME=value\n')
    assert managed_module_content(content, spec, format=format, containers_root=tmp_path / 'containers') is None


def test_cli_preserves_modules_with_undated_historical_inventory(tmp_path, historical_modules):
    _, before, log = historical_modules
    args = [sys.executable, str(SCRIPT), '--repo-root', str(tmp_path), '--log', str(log)]
    for extra in [[], ['--check'], ['--check']]:
        result = subprocess.run(args + extra, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize('invalid,error', [('date', 'invalid container image name'), ('environment', 'Invalid environment name')])
def test_cli_rejects_invalid_current_inventory_without_module_writes(tmp_path, historical_modules, invalid, error):
    _, before, log = historical_modules
    if invalid == 'date':
        log.write_text('demo_latest_latest categories:data,\n')
    else:
        (tmp_path / 'containers/demo_latest_20260629/env.txt').write_text('DEPLOY_ENV_BAD-NAME=value\n')
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '--repo-root', str(tmp_path), '--log', str(log)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert error in result.stderr
    assert {path: path.read_bytes() for path in before} == before
