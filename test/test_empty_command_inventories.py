from dataclasses import replace
import subprocess
import sys

import pytest

from artifact_renderer import legacy_module_content, managed_module_content, read_container_inventory, render_module
from test.support.artifacts import installed, refresh
from test.support.paths import MODULE_RECONCILIATION_SCRIPT as SCRIPT

@pytest.mark.parametrize('format, suffix', [('lua', '.lua'), ('tcl', '')])
@pytest.mark.parametrize('commands', ['', '/opt/bin/demo\nbad/name\nbad command\n'])
def test_cvmfs_cli_reconciles_existing_inventory_without_safe_commands(tmp_path, format, suffix, commands):
    directory, image = installed(tmp_path)
    spec = replace(read_container_inventory(directory), commands=())
    (directory / 'commands.txt').write_text(commands)
    canonical = tmp_path / 'containers/modules/demo' / ('1.0' + suffix)
    canonical.parent.mkdir(parents=True)
    canonical.write_text(legacy_module_content(spec, directory, format=format))
    log = tmp_path / 'log.txt'
    log.write_text(directory.name + ' categories:data,\n')
    args = [sys.executable, str(SCRIPT), '--repo-root', str(tmp_path), '--log', str(log)]

    result = subprocess.run(args, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    public = tmp_path / 'neurodesk-modules/data/demo' / ('1.0' + suffix)
    for module in [canonical, public]:
        content = module.read_text()
        assert content.encode() == render_module(spec, format=format)
        assert 'Commands:' not in content
        assert 'neurodesk-exposed-commands' not in content
        assert image in content
        assert 'TEST_VALUE' in content
        assert 'Help' in content
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    result = subprocess.run(args + ['--check'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('format', ['lua', 'tcl'])
def test_old_empty_inventory_can_migrate_to_nonempty_latest(tmp_path, format):
    old, _ = installed(tmp_path)
    old_spec = replace(read_container_inventory(old), commands=())
    (old / 'commands.txt').write_text('')
    content = legacy_module_content(old_spec, old, format=format)
    latest = old.parent / 'demo_1.0_20261004'
    latest.mkdir()
    (latest / 'commands.txt').write_text('new-command\n')
    (latest / 'env.txt').write_text('DEPLOY_ENV_NEW=BASEPATH/new-value\n')
    (latest / 'README.md').write_text('New help\n')
    spec = read_container_inventory(latest)

    assert managed_module_content(content, spec, format=format, containers_root=old.parent) == render_module(spec, format=format)
    assert managed_module_content(content + '\n# site customization\n', spec, format=format, containers_root=old.parent) is None


@pytest.mark.parametrize('commands', ['', '/opt/bin/demo\nbad/name\nbad command\n'])
@pytest.mark.parametrize('entrypoint', ['renderer', 'refresh', 'check'])
def test_deployment_rejects_inventory_without_safe_commands_without_writes(tmp_path, commands, entrypoint):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    spec = replace(read_container_inventory(directory), commands=())
    (directory / 'commands.txt').write_text(commands)
    for format, suffix in [('lua', '.lua'), ('tcl', '')]:
        (tmp_path / 'containers/modules/demo' / ('1.0' + suffix)).write_bytes(render_module(spec, format=format))
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}

    if entrypoint == 'refresh':
        result = refresh(directory, image)
    else:
        args = [sys.executable, str(directory / 'artifact_renderer.py')]
        args += ['--check-container', str(directory)] if entrypoint == 'check' else [image]
        result = subprocess.run(args, capture_output=True, text=True)

    assert result.returncode != 0, result.stdout + result.stderr
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
