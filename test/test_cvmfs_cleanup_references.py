import shlex
import subprocess
import sys

import pytest

from cvmfs import reconcile_module_files
from test.support.paths import ROOT, MODULE_RECONCILIATION_SCRIPT as SCRIPT

OLD = 'freesurfer_8.2.0_20260818'
OTHER = 'unused_1.0_20260101'


def container(root, name):
    path = root / 'containers' / name
    path.mkdir(parents=True)
    (path / 'commands.txt').write_text('recon-all\n')
    wrapper = path / 'recon-all'
    wrapper.write_text('#!/bin/bash\necho original\n')
    wrapper.chmod(0o755)
    (path / (name + '.simg')).write_bytes(b'image')
    return path


def module(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def cleanup(root, *, fail_scan=False):
    source = (ROOT / 'cvmfs/sync_containers_to_cvmfs.sh').read_text()
    block = source.split('# disable unpacked container versions that no longer exist in log.txt:\n', 1)[1]
    block = block.split('# Existing commands.txt files make completed containers immutable', 1)[0]
    block = block.replace('/cvmfs/neurodesk.ardc.edu.au', str(root))
    setup = '\n'.join([
        'set -euo pipefail',
        f'NEUROCOMMAND_LOCAL_REPO={shlex.quote(str(ROOT))}',
        'declare -A KEEP_IMAGES=([kept]=1)',
        f'open_cvmfs_transaction() {{ echo open >> {shlex.quote(str(root / "transactions"))}; }}',
        'publish_cvmfs_transaction() { :; }',
        'sudo() { "$@"; }',
    ])
    if fail_scan:
        setup += f'\npython3() {{ echo {OLD}; echo scan-failed >&2; return 2; }}'
    return subprocess.run(['bash', '-c', setup + '\n' + block], text=True, capture_output=True)


@pytest.mark.parametrize('relative,content', [
    ('containers/modules/freesurfer/8.2.0.lua', f'prepend_path("PATH", "/legacy/{OLD}")'),
    ('neurodesk-modules/unexpected/retired/0.1.lua', f'setenv("SITE", "/legacy/{OLD}/opt")'),
    ('neurodesk-modules/imaging/freesurfer/8.2.0', f'prepend-path PATH "/legacy/{OLD}"'),
    ('containers/modules/freesurfer/8.2.0.lua', f'local image = pathJoin(root, "{OLD}.simg")'),
])
def test_cleanup_preserves_referenced_build_and_removes_unrelated(tmp_path, relative, content):
    old = container(tmp_path, OLD)
    other = container(tmp_path, OTHER)
    module(tmp_path, relative, content)
    before = (old / 'recon-all').read_bytes()
    result = cleanup(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (old / 'recon-all').read_bytes() == before
    assert (old / (OLD + '.simg')).exists()
    assert not (other / (OTHER + '.simg')).exists()
    assert (other / 'recon-all').read_bytes() != before


def test_cleanup_follows_public_alias_and_handles_absent_payload(tmp_path):
    old = container(tmp_path, OLD)
    (old / (OLD + '.simg')).unlink()
    target = module(tmp_path, 'site-module', f'local dir = "{OLD}"')
    alias = tmp_path / 'neurodesk-modules/custom/retired/old.lua'
    alias.parent.mkdir(parents=True)
    alias.symlink_to(target)
    before = (old / 'recon-all').read_bytes()
    result = cleanup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (old / 'recon-all').read_bytes() == before
    assert not (tmp_path / 'transactions').exists()


@pytest.mark.parametrize('failure', ['dangling', 'cycle', 'directory-cycle', 'unreadable', 'partial-output'])
def test_cleanup_scan_failure_changes_nothing(tmp_path, failure):
    old = container(tmp_path, OLD)
    alias = tmp_path / 'containers/modules/retired/0.1.lua'
    alias.parent.mkdir(parents=True)
    if failure == 'unreadable':
        alias.write_text(f'"{OLD}"')
        alias.chmod(0)
    elif failure == 'directory-cycle':
        alias.symlink_to(alias.parent, target_is_directory=True)
    elif failure != 'partial-output':
        alias.symlink_to(alias if failure == 'cycle' else tmp_path / 'missing')
    before = (old / 'recon-all').read_bytes()
    result = cleanup(tmp_path, fail_scan=failure == 'partial-output')
    assert result.returncode == 2, result.stdout + result.stderr
    assert (old / 'recon-all').read_bytes() == before
    assert (old / (OLD + '.simg')).exists()
    assert not (tmp_path / 'transactions').exists()


def test_reference_inventory_exact_tokens_undated_and_aliases(tmp_path):
    names = [OLD, OTHER, 'legacy_1.0']
    for name in names:
        container(tmp_path, name)
    path = module(tmp_path, 'containers/modules/tool/version',
                  f'"{OLD}.simg" "legacy_1.0" "{OTHER}0" "prefix{OTHER}" "{OTHER}.backup"')
    alias = tmp_path / 'neurodesk-modules/custom/retired/old.lua'
    alias.parent.mkdir(parents=True)
    alias.symlink_to(path)
    refs = reconcile_module_files.referenced_containers(tmp_path)
    assert refs == {name: (path, alias) for name in (OLD, 'legacy_1.0')}
    result = subprocess.run([sys.executable, str(SCRIPT), '--repo-root', str(tmp_path),
                             '--referenced-containers'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [OLD, 'legacy_1.0']


def test_empty_reference_inventory_allows_cleanup(tmp_path):
    old = container(tmp_path, OLD)
    result = cleanup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (old / (OLD + '.simg')).exists()
