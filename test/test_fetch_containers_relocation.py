import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test.test_run_transparent_singularity import write_executable


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "demo_1.0_20260629"


@pytest.fixture
def installation(tmp_path):
    install = tmp_path / "neurodesk"
    install.mkdir()
    for name in ("fetch_containers.sh", "configparser.sh"):
        shutil.copy(ROOT / "neurodesk" / name, install / name)
    shutil.copytree(
        ROOT / "neurodesk/transparent-singularity", install / "transparent-singularity"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    write_executable(bin_dir / "module", "#!/bin/bash\nexit 0\n")
    write_executable(
        bin_dir / "curl",
        '#!/bin/bash\necho "unexpected network call" >> "$CALLS"\nexit 1\n',
    )
    write_executable(
        bin_dir / "singularity",
        """
        #!/bin/bash
        echo "$*" >> "$CALLS"
        if [[ "$1" = version ]]; then
            echo 3.10.0
        elif [[ "$1" = build || "$1" = pull ]]; then
            exit 99
        elif [[ "$*" = *ts_binaryFinder.sh* ]]; then
            [[ "${FAIL_DISCOVERY:-}" = 1 ]] && exit 42
            printf 'demo\\n' > commands.txt
            printf 'DEPLOY_ENV_DEMO=BASEPATH/opt/demo\\n' > env.txt
        elif [[ "$*" = *'cat /README.md'* ]]; then
            echo 'Demo help'
        fi
        """,
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "CVMFS_DISABLE": "1",
    }
    return install, env, calls


def fetch(install, env, containers):
    return subprocess.run(
        ["bash", str(install / "fetch_containers.sh"), "demo", "1.0", "20260629"],
        env={**env, "NEURODESKTOP_LOCAL_CONTAINERS": str(containers)},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("image_kind", ["sif", "sandbox", "symlink"])
def test_refetch_repairs_moved_installation(installation, tmp_path, image_kind):
    install, env, calls = installation
    old = tmp_path / "original"
    deployed = old / IMAGE
    shutil.copytree(install / "transparent-singularity", deployed)
    image = deployed / f"{IMAGE}.simg"
    if image_kind == "sif":
        image.write_text("existing SIF")
    elif image_kind == "sandbox":
        image.mkdir()
        (image / "preserve").write_text("existing sandbox")
    else:
        target = tmp_path / "cached-image"
        target.mkdir()
        (target / "preserve").write_text("cached sandbox")
        image.symlink_to(target, target_is_directory=True)
    generated = subprocess.run(
        ["bash", str(deployed / "run_transparent_singularity.sh"), f"{IMAGE}.simg"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    moved = tmp_path / "moved"
    old.rename(moved)
    calls.write_text("")

    result = fetch(install, env, moved)

    assert result.returncode == 0, result.stdout + result.stderr
    wrapper = moved / IMAGE / "demo"
    module = moved / "modules/demo/1.0.lua"
    assert str(old) not in wrapper.read_text()
    assert str(old) not in module.read_text()
    assert f'prepend_path("PATH", "{moved / IMAGE}")' in module.read_text()
    assert f'setenv("DEMO", "{moved / IMAGE}/{IMAGE}.simg/opt/demo")' in module.read_text()
    subprocess.run([str(wrapper), "argument"], env=env, check=True)
    call_log = calls.read_text()
    assert f"{moved / IMAGE}/{IMAGE}.simg demo argument" in call_log
    assert "unexpected network call" not in call_log
    assert "build " not in call_log
    assert "pull " not in call_log
    moved_image = moved / IMAGE / f"{IMAGE}.simg"
    if image_kind == "sif":
        assert moved_image.read_text() == "existing SIF"
    else:
        assert (moved_image / "preserve").exists()
        assert moved_image.is_symlink() == (image_kind == "symlink")

    before = (wrapper.stat().st_mtime_ns, module.stat().st_mtime_ns)
    calls.write_text("")
    again = fetch(install, env, moved)
    assert again.returncode == 0, again.stdout + again.stderr
    assert before == (wrapper.stat().st_mtime_ns, module.stat().st_mtime_ns)
    assert calls.read_text().splitlines() == [f"exec {moved_image} ls"]


@pytest.mark.parametrize("fail_discovery", [False, True])
def test_refetch_restores_missing_module(installation, tmp_path, fail_discovery):
    install, env, calls = installation
    containers = tmp_path / "containers"
    deployed = containers / IMAGE
    deployed.mkdir(parents=True)
    (deployed / f"{IMAGE}.simg").write_text("existing SIF")

    result = fetch(
        install, {**env, "FAIL_DISCOVERY": "1" if fail_discovery else "0"}, containers
    )

    module = containers / "modules/demo/1.0.lua"
    if fail_discovery:
        assert result.returncode != 0
        assert "Could not inspect executables" in result.stderr
        assert not module.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert f'prepend_path("PATH", "{deployed}")' in module.read_text()
        assert (deployed / "demo").is_file()
    assert "unexpected network call" not in calls.read_text()
