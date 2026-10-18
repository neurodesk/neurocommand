import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from artifact_renderer import read_container_inventory, render_module
from cvmfs import prefetch_filebundles as prefetch
from cvmfs import reconcile_module_files as reconcile
from test.support.module_engines import module_init
from test.support.paths import ROOT
from test.support.shell import clean_env


IMAGE = "fsl_6.0.7.23_20261013"
TRIGGER = ".neurodesk-startup"


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repository with spaces"
    directory = repo / "containers" / IMAGE
    image = directory / (IMAGE + ".simg")
    fsl = image / "opt/fsl-6.0.7.23"
    for relative in ("bin", "etc/fslconf", "lib"):
        (fsl / relative).mkdir(parents=True)
    (directory / "commands.txt").write_text("fsl\nfsleyes\n")
    (directory / "env.txt").write_text("DEPLOY_ENV_FSLDIR=BASEPATH/opt/fsl-6.0.7.23\n")
    for name in ("container_runtime.sh", "fsl", "fsleyes"):
        (directory / name).write_text("#!/bin/bash\n")
    for name in ("fsl", "fsleyes"):
        (fsl / "bin" / name).write_text("#!/bin/bash\n")
    (fsl / "etc/fslconf/fsl.sh").write_text("export FSLOUTPUTTYPE=NIFTI_GZ\n")
    (image / ".singularity.d/env").mkdir(parents=True)
    (image / ".singularity.d/env/90-environment.sh").write_text(
        "export FSLDIR=/opt/fsl-6.0.7.23\n"
    )
    (image / "usr/bin").mkdir(parents=True)
    (image / "usr/bin/bash").write_text("shell\n")
    (image / "bin").symlink_to("/usr/bin")
    (fsl / "data").mkdir()
    (fsl / "data/big-model.nii.gz").write_bytes(b"do not prefetch")
    traces = repo / "traces"
    traces.mkdir()
    for command in ("fsl", "fsleyes"):
        (traces / (command + ".trace")).write_text(
            f'123 execve("/opt/fsl-6.0.7.23/bin/{command}", [], []) = 0\n'
            '123 openat(AT_FDCWD, "/opt/fsl-6.0.7.23/etc/fslconf/fsl.sh", O_RDONLY) = 3</opt/fsl-6.0.7.23/etc/fslconf/fsl.sh>\n'
            '123 openat(AT_FDCWD, "/absent", O_RDONLY) = -1 ENOENT\n'
            '123 openat(AT_FDCWD, "/", O_RDONLY|O_DIRECTORY) = 3</>\n'
            '123 openat(AT_FDCWD, "/tmp/runtime", O_RDONLY) = 3</tmp/runtime>\n'
        )
    log = tmp_path / "log.txt"
    log.write_text(f"{IMAGE} categories:structural imaging,\n")
    return repo, directory, image, fsl, log


def cli(repo, log, *args):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "cvmfs/prefetch_filebundles.py"),
            "--repo-root",
            str(repo),
            "--log",
            str(log),
            "--traces",
            str(repo / "traces"),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_publishes_repository_paths_and_reuses_profile_for_the_same_build(
    repository,
):
    repo, directory, image, fsl, log = repository
    assert cli(repo, log, "--check").returncode == 1
    assert not (directory / TRIGGER).exists()
    result = cli(repo, log)
    assert result.returncode == 0, result.stderr
    manifest = directory / (".cvmfsbundle-" + TRIGGER)
    actual = json.loads(manifest.read_text())
    prefix = f"/containers/{IMAGE}"
    assert actual == {
        "name": "CVMFS_BUNDLE",
        "version": "1.0.0",
        "encoding": "UTF-8",
        "dependencies": sorted(
            [
                *(
                    f"{prefix}/{name}"
                    for name in ("container_runtime.sh", "fsl", "fsleyes")
                ),
                f"{prefix}/{IMAGE}.simg/.singularity.d/env/90-environment.sh",
                f"{prefix}/{IMAGE}.simg/usr/bin/bash",
                *(
                    f"{prefix}/{IMAGE}.simg/opt/fsl-6.0.7.23/bin/{name}"
                    for name in ("fsl", "fsleyes")
                ),
                f"{prefix}/{IMAGE}.simg/opt/fsl-6.0.7.23/etc/fslconf/fsl.sh",
            ]
        ),
    }
    assert (
        directory / TRIGGER
    ).read_bytes() == f"neurodesk-fsl-gui-startup-v1\n{IMAGE}.simg\n".encode()
    before = manifest.stat().st_mtime_ns
    # A valid published profile needs no display, runtime, or previous traces.
    shutil.rmtree(repo / "traces")
    assert cli(repo, log, "--check").returncode == 0
    assert cli(repo, log).returncode == 0
    assert manifest.stat().st_mtime_ns == before
    assert cli(repo, log, "--refresh", "--check").returncode == 1
    (fsl / "etc/fslconf/fsl.sh").unlink()
    assert cli(repo, log, "--check").returncode == 1
    assert manifest.stat().st_mtime_ns == before


@pytest.mark.parametrize(
    "defect", ["missing-file", "symlink-loop", "escape", "sif", "size", "empty-trace"]
)
def test_invalid_dependencies_do_not_publish(repository, monkeypatch, defect):
    repo, directory, image, fsl, log = repository
    if defect == "missing-file":
        (directory / "fsleyes").unlink()
    elif defect in {"symlink-loop", "escape"}:
        (fsl / "bin/fsleyes").unlink()
        (fsl / "bin/fsleyes").symlink_to(
            "fsleyes" if defect == "symlink-loop" else "../../../../../../host"
        )
    elif defect == "sif":
        shutil.rmtree(image)
        image.touch()
    elif defect == "empty-trace":
        (repo / "traces/fsleyes.trace").write_text("")
    else:
        monkeypatch.setattr(prefetch, "MAX_BUNDLE_BYTES", 1)
    with pytest.raises((ValueError, OSError)):
        prefetch.publish(repo, log, traces=repo / "traces")
    assert not (directory / TRIGGER).exists()
    assert not (directory / (".cvmfsbundle-" + TRIGGER)).exists()


def test_selects_only_requested_version_and_latest_kept_build(repository):
    repo, directory, image, fsl, log = repository
    newer = directory.with_name("fsl_6.0.7.23_20261014")
    shutil.copytree(directory, newer, symlinks=True)
    (newer / (IMAGE + ".simg")).rename(newer / (newer.name + ".simg"))
    log.write_text(
        f"{IMAGE} categories:x,\n{newer.name} categories:x,\nfsl_6.0.7.22_20260416 categories:x,\n"
    )
    assert prefetch.publish(repo, log, traces=repo / "traces")
    assert (newer / TRIGGER).is_file()
    assert not (directory / TRIGGER).exists()
    log.write_text("fsl_6.0.7.22_20260416 categories:x,\n")
    assert cli(repo, log, "--check").returncode == 0


def test_missing_latest_install_defers_without_falling_back(repository):
    repo, directory, image, fsl, log = repository
    with log.open("a") as stream:
        stream.write("fsl_6.0.7.23_20261014 categories:x,\n")
    result = cli(repo, log)
    assert result.returncode == 0, result.stderr
    assert "deferred" in result.stderr
    assert not (directory / TRIGGER).exists()


def test_profiles_real_gui_windows_and_records_their_opened_files(
    repository, monkeypatch, tmp_path
):
    for program in ("xvfb-run", "xauth", "xwininfo", "xmessage", "strace"):
        if not shutil.which(program):
            pytest.skip(f"GUI profiling test requires {program}")
    repo, directory, image, fsl, log = repository
    for name in ("fsl", "fsleyes"):
        (fsl / (name + ".conf")).write_text(f"{name} startup resource\n")
    # Use real X11 windows and strace, with a runtime adapter for the test image.
    runtime = tmp_path / "runtime"
    runtime.write_text("""#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
binding = args[args.index('--bind') + 1].split(':')[0]
index = args.index('strace')
image = pathlib.Path(args[index - 1])
trace_args = [item.replace('/neurodesk-prefetch-profile', binding) for item in args[index + 1:-1]]
command = args[-1]
title = 'FSLeyes' if command == 'fsleyes' else 'FSL'
resource = image / 'opt/fsl-6.0.7.23' / (command + '.conf')
os.execvp('strace', ['strace', *trace_args, 'xmessage', '-title', title, '-file', str(resource)])
""")
    runtime.chmod(0o755)
    monkeypatch.setenv("NEURODESK_CONTAINER_RUNTIME", str(runtime))
    assert prefetch.capture_startup(image) == {fsl / "fsl.conf", fsl / "fsleyes.conf"}
    monkeypatch.setenv("NEURODESK_CONTAINER_RUNTIME", "/bin/false")
    with pytest.raises(ValueError, match="exited before GUI startup"):
        prefetch.capture_startup(image)
    assert not (directory / TRIGGER).exists()


@pytest.mark.parametrize(
    "status,write_status,expected",
    [
        (0, 0, ""),
        (1, 0, "open\nwrite\npublish\n"),
        (1, 2, "open\nwrite\nabort\n"),
        (2, 0, ""),
    ],
)
def test_sync_publishes_only_successful_startup_bundles(
    tmp_path, status, write_status, expected
):
    source = (ROOT / "cvmfs/sync_containers_to_cvmfs.sh").read_text()
    block = source[
        source.index("# Reconcile startup bundles") : source.index(
            "# update neurocommand installation for the lxde menus:"
        )
    ]
    calls = tmp_path / "calls"
    setup = f"""
NEUROCOMMAND_LOCAL_REPO={shlex.quote(str(ROOT))}
open_cvmfs_transaction() {{ echo open >> {shlex.quote(str(calls))}; }}
publish_cvmfs_transaction() {{ echo publish >> {shlex.quote(str(calls))}; }}
abort_cvmfs_transaction() {{ echo abort >> {shlex.quote(str(calls))}; }}
python3() {{
    if [[ ${{@: -1}} == --check ]]; then return {status}; fi
    echo write >> {shlex.quote(str(calls))}
    return {write_status}
}}
"""
    result = subprocess.run(
        ["bash", "-c", setup + block], capture_output=True, text=True
    )
    assert result.returncode == (2 if status == 2 or write_status == 2 else 0), (
        result.stderr
    )
    assert (calls.read_text() if calls.exists() else "") == expected


@pytest.mark.parametrize("engine", ["modules", "lmod-tcl", "lmod-lua"])
@pytest.mark.parametrize("public", [False, True])
def test_real_module_opens_trigger_only_on_load(repository, engine, public):
    init = module_init(engine)
    repo, directory, image, fsl, log = repository
    if engine == "lmod-tcl":
        # Lmod 6.6's Tcl converter splits module filenames on spaces.
        destination = repo.with_name("repository")
        repo.rename(destination)
        directory, image, fsl = (
            destination / path.relative_to(repo) for path in (directory, image, fsl)
        )
        repo = destination
    spec = read_container_inventory(directory)
    suffix = ".lua" if engine == "lmod-lua" else ""
    module_root = (
        repo / "containers/modules"
        if not public
        else repo / "neurodesk-modules/structural_imaging"
    )
    module = module_root / "fsl" / ("6.0.7.23" + suffix)
    module.parent.mkdir(parents=True)
    module.write_bytes(render_module(spec, format="lua" if suffix else "tcl"))
    os.mkfifo(directory / TRIGGER)
    prefix = f"set -e\n{init}\nmodule use {shlex.quote(str(module_root))}\n"

    def run(actions):
        return subprocess.run(
            ["bash", "-c", prefix + actions],
            env=clean_env(),
            capture_output=True,
            text=True,
            timeout=15,
        )

    # With no FIFO writer, any accidental open during inspection would block.
    inspect = "module help fsl/6.0.7.23\nmodule show fsl/6.0.7.23\nmodule avail\n"
    if engine.startswith("lmod"):
        inspect += "module spider fsl/6.0.7.23\n"
    result = run(inspect)
    assert result.returncode == 0, result.stderr
    writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,sys; fd=os.open(sys.argv[1],os.O_WRONLY); print('opened',flush=True); os.close(fd)",
            str(directory / TRIGGER),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        result = run(
            "module load fsl/6.0.7.23\nprintf '%s\\n' \"$FSLDIR\"\nmodule unload fsl/6.0.7.23\n"
        )
        assert result.returncode == 0, result.stderr
        assert writer.communicate(timeout=5)[0] == "opened\n"
        assert result.stdout.strip() == str(fsl)
    finally:
        writer.kill() if writer.poll() is None else None
        writer.wait()
    (directory / TRIGGER).unlink()
    result = run(
        "module load fsl/6.0.7.23\nprintf '%s\\n' \"$FSLDIR\"\nmodule unload fsl/6.0.7.23\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(fsl)


def test_reconciler_refreshes_existing_fsl_modules_and_category_copies(repository):
    repo, directory, image, fsl, log = repository
    spec = read_container_inventory(directory)
    module = repo / "containers/modules/fsl/6.0.7.23.lua"
    module.parent.mkdir(parents=True)
    # Recognize a historical module without a startup hook.
    from artifact_renderer import legacy_module_content

    module.write_text(legacy_module_content(spec, directory, format="lua"))
    reconcile.apply_changes(reconcile.plan_module_reconciliation(repo, log))
    expected = render_module(spec, format="lua")
    assert module.read_bytes() == expected
    assert (
        repo / "neurodesk-modules/structural_imaging/fsl/6.0.7.23.lua"
    ).read_bytes() == expected
    assert (repo / "containers/modules/fsl/6.0.7.23").read_bytes() == render_module(
        spec, format="tcl"
    )
