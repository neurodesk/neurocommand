import shlex
import subprocess

from test.support.paths import ROOT

IMAGE = "demo_1.0_beta_20261012"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def checkout(tmp_path, install_status):
    repo = tmp_path / "neurocommand"
    installer = (
        repo / "neurodesk/transparent-singularity/run_transparent_singularity.sh"
    )
    installer.parent.mkdir(parents=True)
    installer.write_text(
        "#!/bin/bash\n"
        f'echo "$1" >> {shlex.quote(str(tmp_path / "installs"))}\n'
        f'touch commands.txt "$1.simg"\nexit {install_status}\n'
    )
    installer.chmod(0o755)
    binfmt = repo / "cvmfs/ensure_binfmt.sh"
    binfmt.parent.mkdir(parents=True)
    binfmt.write_text("#!/bin/bash\n")
    binfmt.chmod(0o755)
    (repo / "cvmfs/log.txt").write_text(f"{IMAGE} categories:data,\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return repo


def sync(tmp_path, repo):
    source = (ROOT / "cvmfs/sync_containers_to_cvmfs.sh").read_text()
    helpers = source[
        source.index("# A failed install is retried") : source.index("LOCKFILE=")
    ]
    loop = source[
        source.index("INSTALL_FAILED=0") : source.index(
            'echo "[INFO] Checking that module files point'
        )
    ]
    root = tmp_path / "cvmfs"
    (root / "containers").mkdir(parents=True, exist_ok=True)
    script = "\n".join(
        [
            f"HOME={shlex.quote(str(tmp_path / 'home'))}",
            f"NEUROCOMMAND_LOCAL_REPO={shlex.quote(str(repo))}",
            helpers,
            "open_cvmfs_transaction() { :; }",
            # Aborting a CVMFS transaction discards the failed install.
            "abort_cvmfs_transaction() { cd /; rm -rf "
            + shlex.quote(str(root / "containers"))
            + '/"$IMAGENAME_BUILDDATE"; }',
            'publish_cvmfs_transaction() { cd "$HOME" 2>/dev/null || cd /; }',
            "ensure_nested_catalog_markers_for_container() { :; }",
            'sudo() { "$@"; }',
            "declare -A KEEP_IMAGES=()",
            loop.replace("/cvmfs/neurodesk.ardc.edu.au", str(root)),
            'echo "failed=$INSTALL_FAILED deferred=$INSTALL_DEFERRED"',
            'echo "tools=${!FAILED_TOOLS[*]}"',
        ]
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    installs = tmp_path / "installs"
    attempts = installs.read_text().splitlines() if installs.exists() else []
    return result.stdout, attempts, root / "containers" / IMAGE


def test_failed_install_is_not_retried_until_the_installer_changes(tmp_path):
    repo = checkout(tmp_path, install_status=1)

    output, attempts, _ = sync(tmp_path, repo)
    assert attempts == [IMAGE]
    assert "failed=1 deferred=0\ntools=demo\n" in output
    failures = tmp_path / "home/sync-failures"
    (failed_log,) = failures.glob(IMAGE + "_*.log")
    assert (failures / "failures.tsv").read_text().split("\t")[1:] == [
        IMAGE,
        str(failed_log) + "\n",
    ]

    output, attempts, _ = sync(tmp_path, repo)
    assert attempts == [IMAGE]
    assert "Skipping " + IMAGE in output
    assert "failed=0 deferred=1\ntools=demo\n" in output

    installer = (
        repo / "neurodesk/transparent-singularity/run_transparent_singularity.sh"
    )
    installer.write_text(installer.read_text().replace("exit 1", "exit 0"))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "fix")

    output, attempts, installed = sync(tmp_path, repo)
    assert attempts == [IMAGE, IMAGE]
    assert "failed=0 deferred=0\ntools=\n" in output
    assert (installed / (IMAGE + ".simg")).exists()
    # Only the failed attempt's output is kept.
    assert list(failures.glob("*.log")) == [failed_log]
    assert not (
        tmp_path / "home/.cache/neurodesk-sync/failed-installs" / IMAGE
    ).exists()


def test_failed_build_with_underscored_version_protects_its_recipe(tmp_path):
    # mark_install_failed records the failure under $HOME.
    source = (ROOT / "cvmfs/sync_containers_to_cvmfs.sh").read_text()
    helpers = source[
        source.index("# A failed install is retried") : source.index("LOCKFILE=")
    ]
    script = "\n".join(
        [
            f"HOME={shlex.quote(str(tmp_path))}",
            "declare -A FAILED_TOOLS=()",
            helpers,
            "mark_install_failed kul-vbg_0.61.0_23112021_beta_20261004",
            "STALE_IMAGE=kul-vbg_0.5_20200101",
            'echo "${FAILED_TOOLS[${STALE_IMAGE%%_*}]+protected}"',
        ]
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.stdout == "protected\n", result.stdout + result.stderr
