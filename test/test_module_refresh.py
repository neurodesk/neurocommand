from pathlib import Path
import json
import os
import shlex
import shutil
import subprocess

import pytest

from cvmfs import reconcile_module_files, reconcile_wrapper_xauthority as reconcile
from test.support.artifacts import installed, refresh
from test.support.module_engines import module_init
from test.support.paths import TRANSPARENT_SINGULARITY
from test.support.shell import clean_env, write_executable

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "demo_1.0_20260629.simg"


def test_refresh_relocates_offline_and_preserves_wrapper_arguments(tmp_path):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    moved_root = tmp_path / "moved install with spaces and 'quotes'"
    shutil.move(str(tmp_path / "containers"), moved_root)
    directory = moved_root / directory.name
    poison = tmp_path / "poison"
    poison.mkdir()
    calls = tmp_path / "unexpected-calls"
    for program in ("curl", "jq", "singularity", "apptainer"):
        write_executable(poison / program, f'#!/bin/bash\necho {program} >> "{calls}"\nexit 99\n')
    env = clean_env(PATH=f"{poison}:{os.environ['PATH']}")
    result = refresh(directory, image, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls.exists()
    before = {p: p.read_bytes() for p in [directory / "demo", moved_root / "modules/demo/1.0", moved_root / "modules/demo/1.0.lua"]}
    assert refresh(directory, image, env).returncode == 0
    assert all(p.read_bytes() == content for p, content in before.items())
    state, _ = reconcile._classify_wrapper(directory, "demo", (directory / "demo").read_bytes())
    assert state is reconcile.WrapperState.FIXED
    edited = (directory / "demo").read_bytes().replace(b'neurodesk_container exec', b'neurodesk_container shell')
    assert reconcile._classify_wrapper(directory, "demo", edited)[0] is reconcile.WrapperState.UNKNOWN
    argv = tmp_path / "argv.json"
    write_executable(poison / "singularity", '#!/usr/bin/env python3\nimport json,os,sys\nopen(os.environ["ARGV"],"w").write(json.dumps(sys.argv[1:]))\n')
    xauth = tmp_path / "auth with spaces"
    xauth.touch()
    result = subprocess.run([str(directory / "demo"), "argument with spaces", "$literal"], env={**env, "NEURODESK_CONTAINER_RUNTIME": "singularity", "ARGV": str(argv), "XAUTHORITY": str(xauth), "TMPDIR": str(tmp_path / "temp space")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    actual = json.loads(argv.read_text())
    assert actual[-4:] == [str(directory / image), "demo", "argument with spaces", "$literal"]
    assert f"{xauth}:{xauth}:ro" in actual
    assert f"{tmp_path / 'temp space'}:/tmp" in actual


@pytest.mark.parametrize("missing", ["commands.txt", "env.txt", IMAGE])
def test_refresh_missing_inputs_does_not_write(tmp_path, missing):
    directory, image = installed(tmp_path)
    (directory / missing).unlink()
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    result = refresh(directory, image)
    assert result.returncode != 0
    assert before == {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert not (tmp_path / "containers/modules").exists()


@pytest.mark.parametrize("kind", ["sandbox", "symlink"])
def test_refresh_accepts_existing_image_forms(tmp_path, kind):
    directory, image = installed(tmp_path)
    (directory / image).unlink()
    if kind == "sandbox":
        (directory / image).mkdir()
    else:
        target = tmp_path / "real image"
        target.touch()
        (directory / image).symlink_to(target)
    assert refresh(directory, image).returncode == 0


def test_fetch_refresh_uses_current_helpers_after_whole_install_moves(tmp_path):
    original = tmp_path / "original"
    shutil.copytree(ROOT / "neurodesk", original)
    directory, image = installed(original)
    (original / "config.ini").write_text(f"installdir={original}\n")
    moved = tmp_path / "moved neurodesk install"
    original.rename(moved)
    directory = moved / "containers" / directory.name
    (directory / "ts_render_artifacts.sh").write_text("exit 99\n")
    poison = tmp_path / "poison"
    poison.mkdir()
    for command in ("curl", "jq", "singularity", "apptainer"):
        write_executable(poison / command, "#!/bin/bash\nexit 99\n")
    result = subprocess.run(["bash", str(moved / "fetch_containers.sh"), "demo", "1.0", "20260629", "--refresh"], env=clean_env(PATH=f"{poison}:{os.environ['PATH']}"), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "neurodesk-artifact-v2" in (moved / "containers/modules/demo/1.0.lua").read_text()
    assert (directory / "manual_module_files/tcl/matlab").is_file()


@pytest.mark.parametrize("engine", ["modules", "lmod-tcl", "lmod-lua"])
def test_real_module_load_help_unload_escaped_values(tmp_path, engine):
    init = module_init(engine)
    directory, image = installed(tmp_path)
    if engine == "lmod-tcl":
        # Lmod 6.6's Tcl-to-Lua converter cannot represent literal backslashes.
        (directory / "env.txt").write_text('DEPLOY_ENV_TEST_VALUE=BASEPATH/a=b "quoted" $d [e] tail\n')
    result = refresh(directory, image)
    assert result.returncode == 0, result.stderr
    module_root = directory.parent / "modules"
    if engine != "lmod-lua":
        (module_root / "demo/1.0.lua").unlink()
    script = f'''
set -e
{init}
module use {shlex.quote(str(module_root))}
module help demo/1.0
module load demo/1.0
python3 -c 'import json,os;print(json.dumps([os.environ["TEST_VALUE"],os.environ["PATH"].split(":")[0]]))'
module unload demo/1.0
[[ ! -v TEST_VALUE ]]
[[ $PATH != {shlex.quote(str(directory))}:* ]]
'''
    result = subprocess.run(["bash", "-c", script], env=clean_env(), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    suffix = 'a=b "quoted" $d [e] tail' if engine == "lmod-tcl" else 'a=b "quoted" $d [e] \\ tail'
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [f"{directory / image}/{suffix}", str(directory)]
    assert 'Help "quoted" $d [error boom] \\ { unmatched' in result.stderr


@pytest.mark.parametrize("engine", ["modules", "lmod-tcl", "lmod-lua"])
@pytest.mark.parametrize("tool,version", [("freesurfer", "7.4"), ("matlab", "2024b")])
@pytest.mark.parametrize("binds", [{}, {"SINGULARITY_BINDPATH": "/original"}, {"APPTAINER_BINDPATH": "/original"}, {"SINGULARITY_BINDPATH": "/one", "APPTAINER_BINDPATH": "/two"}, {"APPTAINER_BINDPATH": ""}])
@pytest.mark.parametrize("has_home", [True, False])
def test_real_manual_bind_paths_load_unload(tmp_path, engine, tool, version, binds, has_home):
    init = module_init(engine)
    directory, image = installed(tmp_path, tool, version)
    (directory / "env.txt").write_text("")
    assert refresh(directory, image).returncode == 0
    module_root = directory.parent / "modules"
    if engine != "lmod-lua":
        (module_root / f"{tool}/{version}.lua").unlink()
    module = f"{tool}/{version}"
    env = {k: v for k, v in clean_env().items() if k not in {"SINGULARITY_BINDPATH", "APPTAINER_BINDPATH", "HOME"}}
    if has_home:
        env["HOME"] = str(tmp_path / "home with spaces")
    env.update(binds)
    script = f'''
set -e
{init}
module use {shlex.quote(str(module_root))}
module load {module}
python3 -c 'import json,os; print(json.dumps({{k:os.environ[k] for k in ["SINGULARITY_BINDPATH","APPTAINER_BINDPATH"] if k in os.environ}}))'
module unload {module}
python3 -c 'import json,os; print(json.dumps({{k:os.environ[k] for k in ["SINGULARITY_BINDPATH","APPTAINER_BINDPATH"] if k in os.environ}}))'
'''
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    loaded, unloaded = [json.loads(line) for line in result.stdout.splitlines()]
    if tool == "matlab":
        additional = f"{env.get('HOME', '/tmp')}:/opt/matlab/R{version}/licenses"
    else:
        additional = "/tmp" + (",/scratch" if Path("/scratch").is_dir() else "")
    assert loaded == ({k: additional + ("," + v if v else "") for k, v in binds.items()} if binds else {"SINGULARITY_BINDPATH": additional})
    # Module engines may remove an originally empty path variable on unload.
    assert {k: v for k, v in unloaded.items() if v} == {k: v for k, v in binds.items() if v}


def test_normal_fetch_existing_image_without_inventories_inspects_without_network(tmp_path):
    neurodesk = tmp_path / "neurodesk with spaces"
    shutil.copytree(ROOT / "neurodesk", neurodesk)
    directory, image = installed(neurodesk)
    (directory / "commands.txt").unlink()
    (directory / "env.txt").unlink()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "network-calls"
    for command in ("curl", "jq"):
        write_executable(bin_dir / command, f'#!/bin/bash\necho {command} >> "{calls}"\nexit 99\n')
    write_executable(bin_dir / "apptainer", "#!/bin/bash\nexit 0\n")
    write_executable(bin_dir / "singularity", '''#!/bin/bash
if [[ " $* " == *" cat /README.md "* ]]; then echo README; fi
for arg in "$@"; do
    if [[ $arg == */ts_binaryFinder.sh ]]; then
        base=$(dirname "$arg")
        printf 'newcommand\\n' > "$base/commands.txt"
        printf 'DEPLOY_ENV_NEW=BASEPATH/new\\n' > "$base/env.txt"
    fi
done
exit 0
''')
    env = clean_env(PATH=f"{bin_dir}:{os.environ['PATH']}", NEURODESKTOP_LOCAL_CONTAINERS=str(neurodesk / "containers"), NEURODESK_CONTAINER_RUNTIME="singularity")
    script = f'''module() {{ return 0; }}
export -f module
bash {shlex.quote(str(neurodesk / "fetch_containers.sh"))} demo 1.0 20260629 ignored-legacy-command
'''
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls.exists()
    assert (directory / "newcommand").is_file()
    assert 'setenv("NEW", ' in (neurodesk / "containers/modules/demo/1.0.lua").read_text()
    assert 'setenv "NEW" ' in (neurodesk / "containers/modules/demo/1.0").read_text()


def test_refresh_keeps_module_uninstall_cleanup_idempotent(tmp_path):
    directory, image = installed(tmp_path)
    assert refresh(directory, image).returncode == 0
    before = (directory / "ts_uninstall.sh").read_text()
    assert refresh(directory, image).returncode == 0
    assert (directory / "ts_uninstall.sh").read_text() == before
    assert before.count("# neurodesk-module-uninstall-begin") == 1
    (directory / "old.sif").touch()
    assert os.access(directory / "ts_uninstall.sh", os.X_OK)
    result = subprocess.run(["bash", "-c", "./ts_uninstall.sh"], cwd=directory, env=clean_env(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (directory.parent / "modules/demo/1.0").exists()
    assert not (directory.parent / "modules/demo/1.0.lua").exists()


@pytest.mark.parametrize("tool", ["freesurfer", "matlab"])
def test_rendered_manual_snippets_are_stable_under_cvmfs_reconciliation(tmp_path, tool):
    directory, image = installed(tmp_path, tool, "7.4")
    assert refresh(directory, image).returncode == 0
    snippets = TRANSPARENT_SINGULARITY / "manual_module_files"
    for module, snippet, is_lua in (("7.4.lua", snippets / tool, True), ("7.4", snippets / "tcl" / tool, False)):
        content = (directory.parent / "modules" / tool / module).read_text()
        assert reconcile_module_files.update_manual_module(
            content, tool=tool, version="7.4", snippet=snippet.read_text(), is_lua=is_lua
        ) == content


def test_legacy_wrapper_does_not_pass_unsupported_env_options(tmp_path):
    directory, image = installed(tmp_path)
    result = subprocess.run(
        ["bash", str(directory / "ts_render_artifacts.sh"), image, "legacy"],
        env=clean_env(), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "singularity", '''#!/bin/bash
for argument in "$@"; do
    [[ "$argument" != --env ]] || exit 42
done
''')
    xauthority = tmp_path / "Xauthority"
    xauthority.touch()
    result = subprocess.run(
        [str(directory / "demo")],
        env=clean_env(PATH=f"{bin_dir}:{os.environ['PATH']}", XAUTHORITY=str(xauthority), NEURODESK_CONTAINER_RUNTIME="singularity"),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    state, _ = reconcile._classify_wrapper(directory, "demo", (directory / "demo").read_bytes())
    assert state is reconcile.WrapperState.FIXED
