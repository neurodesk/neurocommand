from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest

from test.support.artifacts import installed, refresh
from test.support.module_engines import module_init
from test.support.shell import fake_runtime, clean_env

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'neurodesk/transparent-singularity/container_runtime.sh'


@pytest.mark.parametrize('operation', ['pull', 'build', 'exec', 'shell', 'version'])
@pytest.mark.parametrize('level,flags', [('quiet', ['--silent']), ('normal', []), ('debug', ['--debug'])])
def test_runtime_selection_and_argv(tmp_path, operation, level, flags):
    fake_runtime(tmp_path, 'apptainer')
    singularity = fake_runtime(tmp_path, 'singularity')
    env = clean_env(PATH=f'{tmp_path}:{os.environ["PATH"]}', NEURODESK_CONTAINER_LOG_LEVEL=level)
    result = subprocess.run(['bash', str(HELPER), operation, 'space arg', '$literal'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'runtime': 'apptainer', 'argv': flags + [operation, 'space arg', '$literal'], 'nv': [None, None]}
    env['NEURODESK_CONTAINER_RUNTIME'] = str(singularity)
    result = subprocess.run(['bash', str(HELPER), operation], env=env, capture_output=True, text=True)
    assert json.loads(result.stdout)['runtime'] == 'singularity'


@pytest.mark.parametrize('variable,value', [('NEURODESK_CONTAINER_RUNTIME', '/missing runtime'), ('NEURODESK_CONTAINER_LOG_LEVEL', 'invalid')])
def test_bad_runtime_policy_fails_without_fallback(tmp_path, variable, value):
    fake_runtime(tmp_path, 'apptainer')
    result = subprocess.run(['bash', str(HELPER), 'version'], env=clean_env(PATH=f'{tmp_path}:{os.environ["PATH"]}', **{variable: value}), capture_output=True, text=True)
    assert result.returncode == 2
    assert not result.stdout


@pytest.mark.parametrize('mode,nv,flag', [('auto', ['0', '1'], None), ('on', ['1', '1'], '--nv'), ('off', [None, None], '--no-nv')])
def test_gpu_policy_native_overrides_and_legacy_flags(tmp_path, mode, nv, flag):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    runtime = fake_runtime(tmp_path, 'runtime with spaces')
    env = clean_env(NEURODESK_CONTAINER_RUNTIME=str(runtime), NEURODESK_GPU=mode, APPTAINER_NV='0', SINGULARITY_NV='1', neurodesk_singularity_opts='--nv --bind /data')
    result = subprocess.run([str(directory / 'demo'), 'argument with spaces'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual['nv'] == nv
    if flag:
        assert flag in actual['argv']
    if mode == 'off':
        assert '--nv' not in actual['argv']
    assert actual['argv'][-3:] == [str(directory / image), 'demo', 'argument with spaces']


@pytest.mark.parametrize('kind', ['custom-v2', 'disabled', 'nonexecutable', 'symlink'])
def test_refresh_preserves_wrapper_ownership(tmp_path, kind):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    wrapper = directory / 'demo'
    if kind == 'custom-v2':
        wrapper.write_bytes(wrapper.read_bytes() + b'echo custom\n')
    elif kind == 'disabled':
        wrapper.write_text('#!/usr/bin/env bash\necho "This container was disabled due to a known bug or vulnerability."\necho "apptainer pull docker://vnmd/demo"\n')
    elif kind == 'nonexecutable':
        wrapper.chmod(0o644)
    else:
        wrapper.unlink()
        target = tmp_path / 'custom'
        target.write_text('custom\n')
        wrapper.symlink_to(target)
    before = wrapper.read_bytes(), wrapper.lstat().st_mode
    assert refresh(directory, image).returncode == 0
    assert (wrapper.read_bytes(), wrapper.lstat().st_mode) == before


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua'])
@pytest.mark.parametrize('layout', ['canonical', 'category'])
def test_published_tree_is_portable_read_only(tmp_path, engine, layout):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    public = tmp_path / 'neurodesk-modules/data/demo'
    public.mkdir(parents=True)
    for path in (tmp_path / 'containers/modules/demo').iterdir():
        shutil.copy2(path, public / path.name)
    if engine == 'modules':
        for path in tmp_path.rglob('*.lua'):
            path.unlink()
    moved = tmp_path / 'relocated'
    original = tmp_path / 'publication'
    original.mkdir()
    for name in ['containers', 'neurodesk-modules']:
        (tmp_path / name).rename(original / name)
    original.rename(moved)
    directory = moved / 'containers' / directory.name
    runtime = fake_runtime(moved.parent, 'portable-runtime')
    root = moved / ('containers/modules' if layout == 'canonical' else 'neurodesk-modules/data')
    before = {str(p.relative_to(moved)): p.read_bytes() for p in moved.rglob('*') if p.is_file()}
    for path in moved.rglob('*'):
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    script = f'''set -e
{module_init(engine)}
module use '{root}'
module load demo/1.0
demo 'portable argument'
module unload demo/1.0
'''
    result = subprocess.run(['bash', '-c', script], env=clean_env(NEURODESK_CONTAINER_RUNTIME=str(runtime), NEURODESK_CVMFS_ROOT='/does/not/exist'), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual['argv'][-3:] == [str(directory / image), 'demo', 'portable argument']
    assert before == {str(p.relative_to(moved)): p.read_bytes() for p in moved.rglob('*') if p.is_file()}


def test_refresh_migrates_relocated_frozen_wrapper(tmp_path):
    directory, image = installed(tmp_path)
    from wrapper_legacy import _inventory_wrapper
    wrapper = directory / 'demo'
    wrapper.write_bytes(_inventory_wrapper(directory, 'demo'))
    wrapper.chmod(0o755)
    moved = tmp_path / 'moved'
    directory.parent.rename(moved)
    directory = moved / directory.name
    result = refresh(directory, image)
    assert result.returncode == 0, result.stderr
    assert 'neurodesk_container exec' in (directory / 'demo').read_text()
    assert str(tmp_path / 'containers') not in (directory / 'demo').read_text()


@pytest.mark.parametrize('format,suffix', [('lua', '.lua'), ('tcl', '')])
def test_module_generation_advances_metadata_and_preserves_edits(tmp_path, format, suffix):
    from artifact_renderer import read_container_inventory, render_module, managed_module_content, legacy_module_content
    directory, image = installed(tmp_path)
    old = read_container_inventory(directory)
    generated = render_module(old, format=format).decode()
    legacy = legacy_module_content(old, directory, format=format)
    custom = 'setenv("CUSTOM_LICENSE", "/site/license")\n' if format == 'lua' else 'setenv CUSTOM_LICENSE /site/license\n'
    assert managed_module_content(legacy + custom, old, format=format) is None
    assert managed_module_content(generated + custom, old, format=format) is None
    (directory / 'commands.txt').write_text('new-command\n')
    (directory / 'env.txt').write_text('DEPLOY_ENV_NEW=BASEPATH/new\n')
    (directory / 'README.md').write_text('New help')
    new = read_container_inventory(directory)
    assert managed_module_content(generated, new, format=format) == render_module(new, format=format)


def test_reconciliation_preserves_custom_canonical_and_public_modules(tmp_path):
    from artifact_renderer import read_container_inventory, legacy_module_content
    from test.support.cvmfs import reconcile_module_files
    directory, image = installed(tmp_path)
    spec = read_container_inventory(directory)
    canonical = tmp_path / 'containers/modules/demo/1.0.lua'
    public = tmp_path / 'neurodesk-modules/data/demo/1.0.lua'
    canonical.parent.mkdir(parents=True)
    public.parent.mkdir(parents=True)
    canonical.write_text(legacy_module_content(spec, directory, format='lua') + 'setenv("SITE", "/site/canonical")\n')
    public.write_text(legacy_module_content(spec, directory, format='lua') + 'setenv("SITE", "/site/public")\n')
    before = canonical.read_bytes(), public.read_bytes()
    log = tmp_path / 'log.txt'
    log.write_text(directory.name + ' categories: data\n')
    plan = reconcile_module_files.plan_module_reconciliation(tmp_path, log)
    reconcile_module_files.apply_changes(plan)
    assert before == (canonical.read_bytes(), public.read_bytes())
    assert canonical.with_suffix('').is_file()


def test_uninstall_after_relocation_preserves_old_root(tmp_path):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    moved = tmp_path / 'moved containers'
    directory.parent.rename(moved)
    directory = moved / directory.name
    old_module = tmp_path / 'containers/modules/demo/1.0.lua'
    old_module.parent.mkdir(parents=True)
    old_module.write_text('unrelated new installation\n')
    (directory / 'unused.sif').touch()
    result = subprocess.run(['bash', str(directory / 'ts_uninstall.sh')], cwd=directory, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (moved / 'modules/demo/1.0.lua').exists()
    assert not (moved / 'modules/demo/1.0').exists()
    assert old_module.read_text() == 'unrelated new installation\n'


def test_atomic_artifact_failure_preserves_file_and_cleans_temporary(tmp_path, monkeypatch):
    import artifact_renderer
    path = tmp_path / 'module'
    path.write_bytes(b'old content')
    path.chmod(0o640)
    def fail_replace(*args):
        raise OSError('injected publication failure')
    monkeypatch.setattr(artifact_renderer.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='injected'):
        artifact_renderer.write_artifact(path, b'new content')
    assert path.read_bytes() == b'old content'
    assert path.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.iterdir()) == [path]


def test_reconciles_old_inventory_before_advancing_to_new_build(tmp_path):
    from artifact_renderer import read_container_inventory, legacy_module_content
    from test.support.cvmfs import reconcile_module_files
    old, image = installed(tmp_path)
    old_spec = read_container_inventory(old)
    canonical = tmp_path / 'containers/modules/demo/1.0.lua'
    canonical.parent.mkdir(parents=True)
    canonical.write_text(legacy_module_content(old_spec, old, format='lua'))
    latest = old.parent / 'demo_1.0_20261004'
    shutil.copytree(old, latest)
    (latest / 'demo_1.0_20261004.simg').touch()
    (latest / 'commands.txt').write_text('new-command\n')
    (latest / 'env.txt').write_text('DEPLOY_ENV_NEW=BASEPATH/new-value\n')
    (latest / 'README.md').write_text('New help\n')
    log = tmp_path / 'log.txt'
    log.write_text(latest.name + ' categories: data\n')
    changes = reconcile_module_files.plan_module_reconciliation(tmp_path, log)
    reconcile_module_files.apply_changes(changes)
    assert 'new-command' in canonical.read_text()
    assert 'New help' in canonical.read_text()
    assert '20261004.simg' in canonical.read_text()
    assert '20260629.simg' not in canonical.read_text()
