import shlex
import subprocess

import pytest

from artifact_renderer import BundleSpec, ModuleId, publish_bundles
from test.support.artifacts import installed, refresh
from test.support.module_engines import module_init
from test.support.shell import clean_env

def manifest():
    return {'schema_version': 1, 'bundles': [{'name': 'demo-bundle', 'version': '1.0', 'description': 'Demo', 'categories': ['data organisation'], 'dependencies': [{'name': 'first', 'version': '1.0'}, {'name': 'second', 'version': '1.0'}]}]}

def catalog():
    return {name: {'apps': {f'{name} 1.0': {'version': '20260629', 'exec': ''}}} for name in ['first', 'second']}

def bundle_tree(tmp_path, engine):
    for name in ['first', 'second']:
        directory, image = installed(tmp_path, name, '1.0')
        (directory / 'commands.txt').write_text('collision\n' + name + '\n')
        (directory / 'env.txt').write_text('')
        assert refresh(directory, image).returncode == 0
    module_root = tmp_path / 'containers/modules'
    spec = BundleSpec(ModuleId('demo-bundle', '1.0'), (ModuleId('first', '1.0'), ModuleId('second', '1.0')), ('data',), 'Demo')
    shared = BundleSpec(ModuleId('shared', '1.0'), (ModuleId('first', '1.0'),), ('data',), 'Shared')
    publish_bundles((spec, shared), module_root, tmp_path / 'neurodesk-modules')
    if engine != 'lmod-lua':
        for path in tmp_path.rglob('*.lua'): path.unlink()
    return module_root

def run_engine(engine, root, commands):
    init = module_init(engine)
    if engine.startswith('lmod'):
        probe = subprocess.run(['bash', '-c', init + '; echo "$LMOD_VERSION"'], env=clean_env(), capture_output=True, text=True)
        version = probe.stdout.strip().split('.')
        if version and version[0].isdigit() and int(version[0]) < 7:
            pytest.skip('Bundle lifetime requires Lmod depends_on; old Lmod tested separately')
    script = f'set -e\n{init}\nmodule use {shlex.quote(str(root))}\n' + commands
    return subprocess.run(['bash', '-c', script], env=clean_env(), capture_output=True, text=True, timeout=30)
