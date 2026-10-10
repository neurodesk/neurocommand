from pathlib import Path
import json
import shlex
import subprocess
import sys

import pytest

from artifact_renderer import BundleSpec, ModuleId, load_bundles, publish_bundles
from test.support.artifacts import installed, refresh
from test.support.bundles import manifest, catalog, bundle_tree, run_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('defect', ['schema', 'unknown', 'duplicate', 'empty', 'fields', 'collision', 'incompatible', 'unsafe-category'])
def test_manifest_rejects_invalid_composition(tmp_path, defect):
    value = manifest()
    cat = catalog()
    bundle = value['bundles'][0]
    if defect == 'schema': value['schema_version'] = 2
    if defect == 'unknown': bundle['dependencies'][0]['name'] = 'missing'
    if defect == 'duplicate': bundle['dependencies'].append(bundle['dependencies'][0])
    if defect == 'empty': bundle['dependencies'] = []
    if defect == 'fields': bundle['image'] = 'not-a-container'
    if defect == 'collision': bundle['name'] = 'first'
    if defect == 'incompatible': bundle['dependencies'].append({'name': 'first', 'version': '2.0'})
    if defect == 'unsafe-category': bundle['categories'] = ['..']
    path = tmp_path / 'bundles.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): load_bundles(path, cat)


def test_initial_bundle_uses_published_pins_and_preserves_old_versions():
    cat = json.loads((ROOT / 'neurodesk/apps.json').read_text())
    bundles = load_bundles(ROOT / 'neurodesk/bundles.json', cat)
    bundle = bundles[0]
    assert bundle.module == ModuleId('bidstools', '2026.10')
    assert bundle.dependencies == (ModuleId('bidscoin', '4.6.2'), ModuleId('dcm2niix', 'v1.0.20260724'))
    assert cat['bidstools']['apps']
    assert 'bru2nii' in bundle.description


DUMP = '''python3 -c 'import os,json; print(json.dumps(os.environ.get("LOADEDMODULES", "").split(":")))'\n'''


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua', 'lmod-tcl'])
def test_native_dependency_ownership_and_collisions(tmp_path, engine):
    root = bundle_tree(tmp_path, engine)
    script = 'module load demo-bundle/1.0\n' + DUMP
    script += 'command -v collision\nmodule load shared/1.0\nmodule unload demo-bundle/1.0\n' + DUMP
    script += 'module unload shared/1.0\n' + DUMP
    script += 'module load first/1.0\nmodule load demo-bundle/1.0\nmodule unload demo-bundle/1.0\n' + DUMP
    script += 'module unload first/1.0\nmodule load demo-bundle/1.0\nmodule load first/1.0\nmodule unload demo-bundle/1.0\n' + DUMP
    result = run_engine(engine, root, script)
    assert result.returncode == 0, result.stdout + result.stderr
    records = result.stdout.splitlines()
    assert set(json.loads(records[0])) == {'demo-bundle/1.0', 'first/1.0', 'second/1.0'}
    assert 'second_1.0_20260629/collision' in records[1]
    assert set(json.loads(records[2])) == {'first/1.0', 'shared/1.0'}
    assert json.loads(records[3]) == ['']
    assert json.loads(records[4]) == ['first/1.0']
    assert json.loads(records[5]) == ([''] if engine == 'modules' else ['first/1.0'])


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua', 'lmod-tcl'])
@pytest.mark.parametrize('failure', ['missing', 'incompatible', 'image'])
def test_dependency_failure_leaves_no_partial_state(tmp_path, engine, failure):
    root = bundle_tree(tmp_path, engine)
    before = []
    prefix = ''
    if failure == 'missing':
        for path in (root / 'second').iterdir(): path.unlink()
    elif failure == 'image':
        (tmp_path / 'containers/second_1.0_20260629/second_1.0_20260629.simg').unlink()
    else:
        directory, image = installed(tmp_path, 'first', '2.0')
        assert refresh(directory, image).returncode == 0
        if engine != 'lmod-lua': (root / 'first/2.0.lua').unlink()
        prefix = 'module load first/2.0\n'
        before = ['first/2.0']
    script = prefix + 'if module load demo-bundle/1.0; then exit 80; fi\n' + DUMP
    script += '[[ $MODULEPATH == ' + shlex.quote(str(root)) + ':* || $MODULEPATH == ' + shlex.quote(str(root)) + ' ]]\n'
    result = run_engine(engine, root, script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip()) == (before or [''])


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua', 'lmod-tcl'])
def test_category_only_bundle_resolves_canonical_dependencies(tmp_path, engine):
    bundle_tree(tmp_path, engine)
    root = tmp_path / 'neurodesk-modules/data'
    result = run_engine(engine, root, 'module load demo-bundle/1.0\n' + DUMP + 'module unload demo-bundle/1.0\n' + DUMP)
    assert result.returncode == 0, result.stdout + result.stderr
    assert set(json.loads(result.stdout.splitlines()[0])) == {'demo-bundle/1.0', 'first/1.0', 'second/1.0'}
    assert json.loads(result.stdout.splitlines()[1]) == ['']


@pytest.mark.parametrize('engine', ['modules', 'lmod-lua', 'lmod-tcl'])
def test_preloaded_last_dependency_keeps_native_path_order(tmp_path, engine):
    root = bundle_tree(tmp_path, engine)
    result = run_engine(engine, root, 'module load second/1.0\nmodule load demo-bundle/1.0\ncommand -v collision\nmodule unload demo-bundle/1.0\n' + DUMP)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'first_1.0_20260629/collision' in result.stdout.splitlines()[0]
    assert json.loads(result.stdout.splitlines()[1]) == ['second/1.0']


def test_versioned_bundle_menu_merge_and_custom_publication(tmp_path):
    from artifact_renderer import bundle_menu_entries
    first = BundleSpec(ModuleId('demo-bundle', '1.0'), (ModuleId('first', '1.0'),), ('data',), 'First')
    second = BundleSpec(ModuleId('demo-bundle', '2.0'), (ModuleId('second', '1.0'),), ('bids',), 'Second')
    entries = bundle_menu_entries((first, second))
    assert set(entries['demo-bundle']['apps']) == {'demo-bundle 1.0', 'demo-bundle 2.0'}
    root = tmp_path / 'containers/modules'
    publish_bundles((first,), root, tmp_path / 'neurodesk-modules')
    custom = root / 'demo-bundle/1.0.lua'
    custom.write_text(custom.read_text() + 'setenv("SITE", "keep")\n')
    before = custom.read_bytes()
    publish_bundles((first, second), root, tmp_path / 'neurodesk-modules')
    assert custom.read_bytes() == before
    assert (root / 'demo-bundle/2.0.lua').is_file()


def test_tcl_bundle_rejects_lua_only_dependency_without_partial_state(tmp_path):
    root = bundle_tree(tmp_path, 'lmod-lua')
    (root / 'demo-bundle/1.0.lua').unlink()
    (root / 'second/1.0').unlink()
    result = run_engine('modules', root, 'if module load demo-bundle/1.0; then exit 80; fi\n' + DUMP)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip()) == ['']


def test_bundle_check_reports_only_generated_drift(tmp_path):
    spec = BundleSpec(ModuleId('suite', '1'), (ModuleId('first', '1.0'),), (), 'Demo')
    root = tmp_path / 'modules'
    assert publish_bundles((spec,), root, check=True)
    assert not root.exists()
    publish_bundles((spec,), root)
    assert not publish_bundles((spec,), root, check=True)
    (root / 'suite/1.lua').write_text('-- site customization\n')
    assert not publish_bundles((spec,), root, check=True)


@pytest.mark.parametrize('defect', ['schema', 'manifest-missing', 'catalog-missing', 'catalog-list', 'apps-list'])
def test_bundle_cli_check_distinguishes_invalid_metadata_from_drift(tmp_path, defect):
    config = tmp_path / 'bundles.json'
    source = tmp_path / 'apps.json'
    value = manifest()
    if defect == 'schema': value['schema_version'] = 99
    if defect != 'manifest-missing': config.write_text(json.dumps(value))
    cat = [] if defect == 'catalog-list' else catalog()
    if defect == 'apps-list': cat['first']['apps'] = []
    if defect != 'catalog-missing': source.write_text(json.dumps(cat))
    modules = tmp_path / 'modules'
    result = subprocess.run([sys.executable, str(ROOT / 'neurodesk/transparent-singularity/artifact_renderer.py'), '--manifest', str(config), '--catalog', str(source), '--module-root', str(modules), '--check'], capture_output=True, text=True)
    assert result.returncode == 2
    assert '[ERROR]' in result.stderr
    assert 'Traceback' not in result.stderr
    assert not modules.exists()


def test_bundle_cli_check_reports_drift_and_clean_without_writes(tmp_path):
    config = tmp_path / 'bundles.json'
    source = tmp_path / 'apps.json'
    config.write_text(json.dumps(manifest()))
    source.write_text(json.dumps(catalog()))
    modules = tmp_path / 'modules'
    command = [sys.executable, str(ROOT / 'neurodesk/transparent-singularity/artifact_renderer.py'), '--manifest', str(config), '--catalog', str(source), '--module-root', str(modules)]
    assert subprocess.run(command + ['--check'], capture_output=True).returncode == 1
    assert not modules.exists()
    assert subprocess.run(command, capture_output=True).returncode == 0
    before = {p: p.read_bytes() for p in modules.rglob('*') if p.is_file()}
    assert subprocess.run(command + ['--check'], capture_output=True).returncode == 0
    assert before == {p: p.read_bytes() for p in modules.rglob('*') if p.is_file()}
