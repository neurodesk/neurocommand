import json
import shlex
import subprocess

import pytest

from artifact_renderer import legacy_module_content, read_container_inventory, render_module
from cvmfs import reconcile_module_files, reconcile_wrapper_xauthority as reconcile
from test.support.artifacts import installed, refresh
from test.support.bundles import run_engine
from test.support.launchers import launcher_tree
from test.support.shell import clean_env, fake_runtime
from test.support.wrappers import fixed_wrapper, make_container

@pytest.mark.parametrize('dangling', [False, True])
def test_canonical_tcl_symlink_survives_synthesis(tmp_path, dangling):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    target = tmp_path / 'site-module'
    if not dangling:
        target.write_text('# site module\n')
    canonical = tmp_path / 'containers/modules/demo/1.0'
    canonical.unlink()
    canonical.symlink_to(target)
    log = tmp_path / 'log.txt'
    log.write_text(directory.name + ' categories:data,\n')
    changes = reconcile_module_files.plan_module_reconciliation(tmp_path, log)
    reconcile_module_files.apply_changes(changes)
    assert canonical.is_symlink()
    assert canonical.readlink() == target
    if not dangling:
        assert target.read_text() == '# site module\n'
    assert reconcile_module_files.plan_module_reconciliation(tmp_path, log) == []


@pytest.mark.parametrize('existing', [False, True])
def test_helper_edit_after_preflight_is_preserved(tmp_path, monkeypatch, existing):
    first, second = tmp_path / 'first.py', tmp_path / 'second.py'
    if existing:
        second.write_bytes(b'old helper')
    before = reconcile._read_regular_file(second) if existing else None
    plan = reconcile.ReconciliationPlan((), (), ((first, None, b'first'), (second, before, b'second')))
    write = reconcile.write_artifact
    def edit_next(path, content, executable):
        write(path, content, executable)
        if path == first:
            second.write_bytes(b'site edit')
    monkeypatch.setattr(reconcile, 'write_artifact', edit_next)
    with pytest.raises(RuntimeError, match='Helper changed since planning'):
        reconcile.apply_wrapper_plan(plan)
    assert first.read_bytes() == b'first'
    assert second.read_bytes() == b'site edit'


def test_helper_only_cli_counts_all_changed_files(tmp_path, capsys):
    container = make_container(tmp_path, ['demo'])
    wrapper = container / 'demo'
    wrapper.write_bytes(reconcile.render_wrapper(container.name + '.simg', 'demo'))
    wrapper.chmod(0o755)
    plan = reconcile.plan_wrapper_reconciliation(tmp_path)
    assert not plan.rewrites and len(plan.helpers) == 5
    assert reconcile.main(['--repo-root', str(tmp_path), '--check']) == 1
    assert 'would change 5 file(s)' in capsys.readouterr().out
    assert reconcile.main(['--repo-root', str(tmp_path)]) == 0
    assert 'changed 5 file(s)' in capsys.readouterr().out
    assert reconcile.plan_wrapper_reconciliation(tmp_path).is_clean


def test_fixed_wrapper_fixture_is_recognized_before_edit(tmp_path):
    container = make_container(tmp_path, ['demo'])
    state, _ = reconcile._classify_wrapper(container, 'demo', fixed_wrapper(container, 'demo').encode())
    assert state is reconcile.WrapperState.LEGACY


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua'])
def test_named_command_from_historical_path_only_module(tmp_path, engine):
    install, root, runtime = launcher_tree(tmp_path, engine)
    directory = install / 'containers/first_1.0_20260629'
    format = 'lua' if engine == 'lmod-lua' else 'tcl'
    module = root / 'first' / ('1.0.lua' if format == 'lua' else '1.0')
    module.write_text(legacy_module_content(read_container_inventory(directory), directory, format=format))
    command = f'CVMFS_DISABLE=true NEURODESKTOP_LOCAL_CONTAINERS={shlex.quote(str(install / "containers"))} NEURODESK_CONTAINER_RUNTIME={shlex.quote(str(runtime))} bash {shlex.quote(str(install / "fetch_and_run.sh"))} first 1.0 first "space arg"\n'
    result = run_engine(engine, root, command)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['argv'][-2:] == ['first', 'space arg']


def test_lua_control_bytes_and_literal_unicode_escape_load(tmp_path):
    directory, image = installed(tmp_path)
    value = 'escape:\x1b[31m; nul-free-controls:\x01\x02\x0b\x1f; literal:\\u001b; digit:\x017'
    (directory / 'env.txt').write_text('DEPLOY_ENV_CONTROL=' + value + '\n')
    (directory / 'README.md').write_text('ANSI \x1b[31m text')
    assert refresh(directory, image).returncode == 0
    result = run_engine('lmod-lua', tmp_path / 'containers/modules', "module load demo/1.0\npython3 -c 'import os,json; print(json.dumps(os.environ[\"CONTROL\"]))'\n")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == value


@pytest.mark.parametrize('legacy', [False, True])
def test_wrapper_symlink_passes_resolved_image_and_arguments(tmp_path, legacy):
    directory, image = installed(tmp_path / "install with 'quotes'")
    assert refresh(directory, image).returncode == 0
    (directory / 'demo').write_bytes(reconcile.render_wrapper(image, 'demo', legacy=legacy))
    link = tmp_path / 'linked command'
    link.symlink_to(directory / 'demo')
    runtime = fake_runtime(tmp_path, 'runtime')
    result = subprocess.run([str(link), 'space arg', '$literal'], env=clean_env(NEURODESK_CONTAINER_RUNTIME=str(runtime)), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['argv'][-4:] == [str(directory / image), 'demo', 'space arg', '$literal']


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('entrypoint', ['renderer', 'reconciler'])
def test_old_v2_wrapper_remains_upgradable(tmp_path, legacy, entrypoint):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    prefix = 'NEURODESK_CONTAINER_LEGACY_ENV=1 ' if legacy else ''
    old = ('''#!/usr/bin/env bash
# neurodesk-artifact-v2
_neurodesk_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source "$_neurodesk_dir/container_runtime.sh" || exit 2
''' + prefix + 'neurodesk_container exec "$_neurodesk_dir/"' + shlex.quote(image) + ' demo "$@"\n').encode()
    wrapper = directory / 'demo'
    wrapper.write_bytes(old)
    if entrypoint == 'renderer':
        result = refresh(directory, image)
        assert result.returncode == 0, result.stderr
        assert 'Preserving customized wrapper' not in result.stderr
    else:
        plan = reconcile.plan_wrapper_reconciliation(tmp_path)
        assert not plan.diagnostics
        reconcile.apply_wrapper_plan(plan)
    assert wrapper.read_bytes() == reconcile.render_wrapper(image, 'demo')


def test_legacy_module_recognition_keeps_historical_json_escaping(tmp_path):
    directory, image = installed(tmp_path)
    (directory / 'env.txt').write_text('DEPLOY_ENV_CONTROL=\x1b[31m\\u001b\n')
    spec = read_container_inventory(directory)
    old = legacy_module_content(spec, directory, format='lua')
    assert 'setenv("CONTROL", "\\u001b[31m\\\\u001b")' in old
    from artifact_renderer import managed_module_content
    assert managed_module_content(old, spec, format='lua') == render_module(spec, format='lua')


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua'])
def test_v2_missing_image_still_blocks_named_command(tmp_path, engine):
    install, root, runtime = launcher_tree(tmp_path, engine)
    (install / 'containers/first_1.0_20260629/first_1.0_20260629.simg').unlink()
    result = run_engine(engine, root, f'CVMFS_DISABLE=true NEURODESKTOP_LOCAL_CONTAINERS={shlex.quote(str(install / "containers"))} bash {shlex.quote(str(install / "fetch_and_run.sh"))} first 1.0 echo SHOULD_NOT_RUN\n')
    assert result.returncode != 0
    assert 'Missing Neurodesk image' in result.stderr
    assert 'SHOULD_NOT_RUN' not in result.stdout


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua'])
@pytest.mark.parametrize('arguments', ['--container-shell', '20260629 true'])
def test_legacy_module_requires_identity_for_image_specific_launch(tmp_path, engine, arguments):
    install, root, _ = launcher_tree(tmp_path, engine)
    directory = install / 'containers/first_1.0_20260629'
    format = 'lua' if engine == 'lmod-lua' else 'tcl'
    module = root / 'first' / ('1.0.lua' if format == 'lua' else '1.0')
    module.write_text(legacy_module_content(read_container_inventory(directory), directory, format=format))
    (install / 'fetch_containers.sh').write_text('#!/bin/bash\nexit 0\n')
    result = run_engine(engine, root, f'CVMFS_DISABLE=true NEURODESKTOP_LOCAL_CONTAINERS={shlex.quote(str(install / "containers"))} bash {shlex.quote(str(install / "fetch_and_run.sh"))} first 1.0 {arguments}\n')
    assert result.returncode == 2
    assert 'no valid image identity' in result.stderr
