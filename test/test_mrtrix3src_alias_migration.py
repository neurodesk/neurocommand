from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys

import pytest

from artifact_renderer import ModuleId, identity_variable, managed_module_content, read_container_inventory
from test.support.artifacts import installed, refresh
from test.support.module_engines import module_init
from test.support.paths import MODULE_RECONCILIATION_SCRIPT as SCRIPT
from test.support.shell import clean_env, write_executable

FIXTURES = Path(__file__).parent / 'fixtures/mrtrix3src-alias'
FORMATS = [('lua', 'latest.lua'), ('tcl', 'latest')]
DIGESTS = {
    'latest.lua': 'c4d36099a5110a37d658056c169eb63ede0bff905fb70e4a58f33db867ae9f8d',
    'latest': '188518341f9a5934a186bd88d3961c55b377af850336c0e5bb031f3b47645636',
}


def selected_container(root, date='20260527'):
    directory, _ = installed(root, 'mrtrix3src', 'latest')
    target = directory.with_name(f'mrtrix3src_latest_{date}')
    directory.rename(target)
    (target / (directory.name + '.simg')).rename(target / (target.name + '.simg'))
    for filename in ('README.md', 'commands.txt', 'env.txt'):
        shutil.copyfile(FIXTURES / 'selected-inventory' / filename, target / filename)
    return target


@pytest.fixture
def repository(tmp_path):
    directory = selected_container(tmp_path)
    shutil.copytree(FIXTURES / 'undated-inventory', tmp_path / 'containers/mrtrix3src_latest_latest')
    paths = []
    for root in ('containers/modules', 'neurodesk-modules/diffusion_imaging'):
        for _, filename in FORMATS:
            path = tmp_path / root / 'mrtrix3src' / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((FIXTURES / filename).read_bytes())
            paths.append(path)
    log = tmp_path / 'log.txt'
    log.write_text(f'{directory.name} categories:diffusion imaging,\n')
    return directory, paths, log


def reconcile_cli(root, log, check=False):
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--repo-root', str(root), '--log', str(log)]
        + (['--check'] if check else []), capture_output=True, text=True,
    )


@pytest.mark.parametrize('format,filename', FORMATS)
def test_exact_public_alias_gets_selected_inventory(repository, format, filename):
    directory, _, _ = repository
    original = (FIXTURES / filename).read_bytes()
    assert hashlib.sha256(original).hexdigest() == DIGESTS[filename]
    spec = read_container_inventory(directory)
    assert len(spec.commands) == 130
    migrated = managed_module_content(original.decode(), spec, format=format, containers_root=directory.parent)
    assert migrated is not None, 'The exact reviewed alias must acquire a v2 replacement'
    assert b'neurodesk-artifact-v2' in migrated
    assert spec.image_basename.encode() in migrated
    assert b'mrtrix3src_latest_latest' not in migrated
    assert b'fsleyes' not in migrated and b'fslmaths' not in migrated
    assert b'MRtrix3 provides a set of tools' in migrated
    assert identity_variable(ModuleId('mrtrix3src', 'latest')).encode() in migrated


@pytest.mark.parametrize('format,filename', FORMATS)
@pytest.mark.parametrize('wrong', ['name', 'version', 'format'])
def test_exact_alias_does_not_authorize_another_identity(repository, format, filename, wrong):
    directory, _, _ = repository
    spec = read_container_inventory(directory)
    if wrong == 'name':
        spec = replace(spec, module=ModuleId('other', 'latest'))
    elif wrong == 'version':
        spec = replace(spec, module=ModuleId('mrtrix3src', '1.0'))
    else:
        format = 'tcl' if format == 'lua' else 'lua'
    assert managed_module_content((FIXTURES / filename).read_bytes().decode(), spec, format=format, containers_root=directory.parent) is None


@pytest.mark.parametrize('format,filename', FORMATS)
@pytest.mark.parametrize('edit', ['comment', 'whitespace', 'crlf', 'path', 'help', 'environment'])
def test_edited_alias_does_not_gain_ownership(repository, format, filename, edit):
    directory, _, _ = repository
    original = (FIXTURES / filename).read_bytes()
    edited = edited_alias(original, format, edit)
    assert edited != original
    assert managed_module_content(edited.decode(), read_container_inventory(directory), format=format, containers_root=directory.parent) is None


def edited_alias(original, format, edit):
    if edit == 'crlf':
        return original.replace(b'\n', b'\r\n')
    if edit == 'path':
        return original.replace(b'/containers/', b'/site-containers/')
    if edit == 'help':
        return original.replace(b'help', b'help edited', 1) if format == 'lua' else original + b'proc ModulesHelp {} { puts stderr "site help" }\n'
    if edit == 'environment':
        return original + (b'setenv("SITE", "yes")\n' if format == 'lua' else b'setenv SITE yes\n')
    return original + (b'\n' if edit == 'whitespace' else b'# site edit\n')


def test_reconciliation_check_apply_check_repairs_four_files(tmp_path, repository):
    directory, paths, log = repository
    before = {path: path.read_bytes() for path in paths}
    check = reconcile_cli(tmp_path, log, check=True)
    assert check.returncode == 1, check.stdout + check.stderr
    assert {path: path.read_bytes() for path in paths} == before
    applied = reconcile_cli(tmp_path, log)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    for path in paths:
        assert b'neurodesk-artifact-v2' in path.read_bytes()
        assert directory.name.encode() in path.read_bytes()
        assert b'mrtrix3src_latest_latest' not in path.read_bytes()
    after = {path: path.read_bytes() for path in paths}
    assert reconcile_cli(tmp_path, log, check=True).returncode == 0
    assert {path: path.read_bytes() for path in paths} == after


@pytest.mark.parametrize('edit', ['comment', 'crlf'])
def test_refresh_and_check_preserve_edited_bytes(tmp_path, repository, edit):
    directory, paths, log = repository
    image = directory.name + '.simg'
    for path in paths:
        path.unlink()
    result = refresh(directory, image)
    assert result.returncode == 0, result.stdout + result.stderr
    before = {}
    for path in paths:
        format = 'lua' if path.suffix == '.lua' else 'tcl'
        content = edited_alias((FIXTURES / path.name).read_bytes(), format, edit)
        path.write_bytes(content)
        before[path] = content
    check = subprocess.run([sys.executable, str(directory / 'artifact_renderer.py'), '--check-container', str(directory)], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    assert refresh(directory, image).returncode == 0
    assert reconcile_cli(tmp_path, log, check=True).returncode == 0
    assert reconcile_cli(tmp_path, log).returncode == 0
    assert {path: path.read_bytes() for path in paths} == before


def test_refresh_and_check_recognize_exact_alias(repository):
    directory, paths, _ = repository
    image = directory.name + '.simg'
    for path in paths:
        path.unlink()
    assert refresh(directory, image).returncode == 0
    for path in paths[:2]:
        path.write_bytes((FIXTURES / path.name).read_bytes())
    check = subprocess.run([sys.executable, str(directory / 'artifact_renderer.py'), '--check-container', str(directory)], capture_output=True, text=True)
    assert check.returncode == 1, 'Exact legacy aliases require refresh despite current wrappers'
    assert refresh(directory, image).returncode == 0
    assert all(b'neurodesk-artifact-v2' in path.read_bytes() for path in paths[:2])


def test_later_build_advances_owned_aliases_preserving_edits_and_symlinks(tmp_path, repository):
    _, paths, log = repository
    assert reconcile_cli(tmp_path, log).returncode == 0
    assert all(b'neurodesk-artifact-v2' in p.read_bytes() for p in paths)
    edited, linked = paths[0], paths[3]
    edited.write_bytes(edited.read_bytes() + b'-- site customization\n')
    edited_bytes = edited.read_bytes()
    target = tmp_path / 'site-module'
    target.write_text('# site controlled module\n')
    linked.unlink()
    linked.symlink_to(target)
    newer = selected_container(tmp_path, '20260704')
    log.write_text(log.read_text() + f'{newer.name} categories:diffusion imaging,\n')
    result = reconcile_cli(tmp_path, log)
    assert result.returncode == 0, result.stdout + result.stderr
    assert edited.read_bytes() == edited_bytes
    assert linked.is_symlink() and linked.read_text() == '# site controlled module\n'
    for path in (paths[1], paths[2]):
        assert newer.name.encode() in path.read_bytes()
    assert reconcile_cli(tmp_path, log, check=True).returncode == 0


@pytest.mark.parametrize('engine', ['modules', 'lmod-tcl', 'lmod-lua'])
def test_migrated_alias_loads_selected_identity_and_runs_wrapper(tmp_path, repository, engine):
    init = module_init(engine)
    directory, paths, log = repository
    image = directory / (directory.name + '.simg')
    assert reconcile_cli(tmp_path, log).returncode == 0
    assert all(b'neurodesk-artifact-v2' in p.read_bytes() for p in paths)
    result = refresh(directory, image.name)
    assert result.returncode == 0, result.stdout + result.stderr
    module_root = tmp_path / 'neurodesk-modules/diffusion_imaging'
    if engine != 'lmod-lua':
        (module_root / 'mrtrix3src/latest.lua').unlink()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    write_executable(bin_dir / 'singularity', '#!/usr/bin/env python3\nimport json,os,sys\nopen(os.environ["ARGV"],"w").write(json.dumps(sys.argv[1:]))\n')
    argv = tmp_path / 'argv.json'
    variable = identity_variable(ModuleId('mrtrix3src', 'latest'))
    script = f'''
set -e
{init}
module use {shlex.quote(str(module_root))}
module load mrtrix3src/latest
python3 -c 'import json,os; print(json.dumps([os.environ["{variable}"], os.environ["PATH"].split(":")[0]]))'
mrview 'argument with spaces' '$literal'
module unload mrtrix3src/latest
[[ ! -v {variable} ]]
'''
    result = subprocess.run(['bash', '-c', script], env=clean_env(PATH=f'{bin_dir}:{os.environ["PATH"]}', ARGV=str(argv), NEURODESK_CONTAINER_RUNTIME='singularity'), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [str(image), str(directory)]
    assert json.loads(argv.read_text())[-4:] == [str(image), 'mrview', 'argument with spaces', '$literal']
