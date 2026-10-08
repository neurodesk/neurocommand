import os
import re
import subprocess
from pathlib import Path

import pytest

from test.test_run_transparent_singularity import write_executable


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "docker/before-notebook.d/01-cvmfs-mount.sh"


@pytest.fixture
def startup(tmp_path):
    source = re.sub(
        r"(?<![\w.-])/(?:dev/fuse|home|cvmfs|etc)",
        lambda match: str(tmp_path) + match.group(),
        HOOK.read_text(),
    )
    hook = tmp_path / "hook.sh"
    hook.write_text(source)
    fuse = tmp_path / "dev/fuse"
    fuse.parent.mkdir()
    fuse.touch()
    (tmp_path / "home/jovyan").mkdir(parents=True)
    config = tmp_path / "etc/cvmfs/config.d"
    config.mkdir(parents=True)
    (config / "neurodesk.ardc.edu.au.conf").write_text("bundled default")
    (config / "neurodesk.ardc.edu.au.conf.cdn.america").write_text("cdn america")
    autofs = tmp_path / "etc/init.d/autofs"
    autofs.parent.mkdir(parents=True)
    write_executable(autofs, "#!/bin/bash\nexit 0\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    write_executable(bin_dir / "chown", "#!/bin/bash\nexit 0\n")
    write_executable(bin_dir / "service", "#!/bin/bash\nexit 1\n")
    write_executable(
        bin_dir / "nslookup",
        '#!/bin/bash\necho nslookup >> "$CALLS"\nexit 1\n',
    )
    write_executable(
        bin_dir / "curl",
        '''#!/bin/bash
echo curl >> "$CALLS"
if [[ ${OFFLINE:-0} == 1 ]]; then
    echo "0.000 000"
    exit 6
fi
case "${@: -1}" in
    *jetstream*|*cvmfs.neurodesk.org*) echo "0.100 200";;
    *) echo "0.200 200";;
esac
''',
    )
    write_executable(
        bin_dir / "mount",
        '''#!/bin/bash
echo mount >> "$CALLS"
[[ ${OFFLINE:-0} == 1 ]] && exit 1
mkdir -p "${@: -1}/neurodesk-modules"
''',
    )
    env = {
        **{key: value for key, value in os.environ.items() if key != "BASH_ENV"},
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "NB_USER": "jovyan",
        "NEURODESK_CVMFS_DISABLE": "0",
        "NEURODESK_SKIP_REGION_PROBE": "0",
    }

    def run(**overrides):
        result = subprocess.run(
            ["bash", str(hook)],
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result, calls.read_text().splitlines() if calls.exists() else []

    return run, tmp_path


def test_unrelated_dns_failure_does_not_prevent_mount(startup):
    run, root = startup
    result, calls = run()

    assert "mount" in calls
    assert "CVMFS ready (manual mount)" in result.stdout
    assert (root / "etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf").read_text() == "cdn america"


def test_offline_probes_still_reach_local_container_fallback(startup):
    run, root = startup
    result, calls = run(OFFLINE="1")

    assert "mount" in calls
    assert "Falling back to local containers" in result.stdout
    assert (root / "etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf").read_text() == "bundled default"


@pytest.mark.parametrize("reason", ["disabled", "missing-fuse", "already-mounted"])
def test_startup_skips_mount_when_unnecessary(startup, reason):
    run, root = startup
    overrides = {}
    if reason == "disabled":
        overrides["NEURODESK_CVMFS_DISABLE"] = "1"
    elif reason == "missing-fuse":
        (root / "dev/fuse").unlink()
    else:
        (root / "cvmfs/neurodesk.ardc.edu.au/neurodesk-modules").mkdir(parents=True)

    _, calls = run(**overrides)

    assert calls == []


def test_skipping_region_probe_uses_bundled_config(startup):
    run, root = startup
    result, calls = run(NEURODESK_SKIP_REGION_PROBE="1")

    assert calls == ["mount"]
    assert "CVMFS ready (manual mount)" in result.stdout
    assert (root / "etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf").read_text() == "bundled default"


@pytest.mark.parametrize("mode", ["offline", "skip-probe"])
def test_fresh_image_gets_default_config_without_successful_probes(startup, mode):
    run, root = startup
    config = root / "etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf"
    config.unlink()

    overrides = {"OFFLINE": "1"} if mode == "offline" else {"NEURODESK_SKIP_REGION_PROBE": "1"}
    result, calls = run(**overrides)

    assert "mount" in calls
    assert config.read_text() == "cdn america"
    if mode == "offline":
        assert "Falling back to local containers" in result.stdout
    else:
        assert "CVMFS ready (manual mount)" in result.stdout
