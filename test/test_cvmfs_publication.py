import os
import subprocess
from pathlib import Path

import pytest

from cvmfs import check_publication as publication
from cvmfs.retention_inventory import retained_ids


@pytest.mark.parametrize(
    "text",
    ["", "../bad categories:a,", "/bad categories:a,", "bad", "ok categories:a,\n\n"],
)
def test_invalid_inventory_fails_closed(text):
    with pytest.raises(publication.PublicationUnavailable):
        publication.inventory_ids(text)


def test_inventory_preserves_exact_ids_and_deduplicates():
    assert publication.inventory_ids(
        "a_1.0+build categories:x,\na_1.0+build categories:y,"
    ) == frozenset({"a_1.0+build"})


def test_snapshot_requires_real_directory_and_completion_marker(tmp_path):
    root = tmp_path / "containers"
    root.mkdir()
    for name in ("ready", "incomplete", "linked-marker"):
        (root / name).mkdir()
    (root / "ready/commands.txt").write_text("tool\n")
    (root / "linked-marker/commands.txt").symlink_to(root / "ready/commands.txt")
    (root / "linked-dir").symlink_to(root / "ready", target_is_directory=True)
    assert publication._inspect_snapshot(
        tmp_path,
        frozenset({"ready", "incomplete", "linked-marker", "linked-dir", "absent"}),
    ) == ("absent", "incomplete", "linked-dir", "linked-marker")


def test_snapshot_io_failure_is_not_missing(tmp_path, monkeypatch):
    (tmp_path / "containers").mkdir()
    original = Path.lstat

    def fail(path):
        if path.name == "tool":
            raise OSError("catalog read failed")
        return original(path)

    monkeypatch.setattr(Path, "lstat", fail)
    with pytest.raises(OSError, match="catalog read failed"):
        publication._inspect_snapshot(tmp_path, frozenset({"tool"}))


@pytest.fixture
def client(tmp_path, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    script = binary / "cvmfs2"
    script.write_text("""#!/usr/bin/env python3
import os, pathlib, signal, sys, time
root = pathlib.Path(sys.argv[-1])
pathlib.Path(os.environ['CLIENT_ROOT']).write_text(str(root))
config = pathlib.Path(sys.argv[sys.argv.index('-o')+1].split('=',1)[1]).read_text()
pathlib.Path(os.environ['CLIENT_CONFIG']).write_text(config)
if os.environ.get('CLIENT_FAIL'):
    sys.exit(16)
if not os.environ.get('CLIENT_HANG'):
    (root/'containers'/'ready').mkdir(parents=True)
    (root/'containers'/'ready'/'commands.txt').write_text('tool')
    attrs = {'revision':'123','root_hash':'a'*40,'fqrn':'neurodesk.ardc.edu.au', 'host':'http://stratum0.neurodesk.cloud.edu.au/cvmfs/neurodesk.ardc.edu.au'}
    for key,value in attrs.items():
        os.setxattr(root, 'user.'+key, value.encode())
    (root/'mounted').touch()
if os.environ.get('CLIENT_CANCEL'):
    os.kill(os.getppid(), signal.SIGTERM)
while True:
    time.sleep(1)
""")
    script.chmod(0o755)
    unmount = binary / "fusermount"
    unmount.write_text(
        '#!/usr/bin/env python3\nimport pathlib,sys\n(pathlib.Path(sys.argv[-1])/"mounted").unlink()\n'
    )
    unmount.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}:{os.environ['PATH']}")
    monkeypatch.setenv("CLIENT_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("CLIENT_CONFIG", str(tmp_path / "config"))
    monkeypatch.setattr(
        publication.os.path, "ismount", lambda root: (Path(root) / "mounted").exists()
    )
    log = tmp_path / "log"
    log.write_text("ready categories:a,\nmissing categories:b,\n")
    return log


def test_client_process_boundary_and_cleanup(client, tmp_path):
    result = publication.inspect_publication(client)
    assert result == publication.PublishedInventory("123", "a" * 40, ("missing",))
    assert not Path((tmp_path / "root").read_text()).exists()
    config = (tmp_path / "config").read_text()
    assert "CVMFS_IGNORE_SIGNATURE=no\n" in config
    assert "CVMFS_AUTO_UPDATE=no\n" in config
    assert "CVMFS_RELOAD_SOCKETS=/" in config
    assert "CVMFS_ROOT_HASH" not in config


@pytest.mark.parametrize("mode", ["CLIENT_FAIL", "CLIENT_HANG"])
def test_client_failure_and_deadline_cleanup(client, tmp_path, monkeypatch, mode):
    monkeypatch.setenv(mode, "1")
    monkeypatch.setattr(publication, "PROBE_SECONDS", 1)
    with pytest.raises(publication.PublicationUnavailable):
        publication.inspect_publication(client)
    assert not Path((tmp_path / "root").read_text()).exists()


def test_changed_snapshot_fails_closed(client, monkeypatch):
    real = publication._identity
    count = 0

    def identity(root):
        nonlocal count
        count += 1
        revision, root_hash = real(root)
        return str(int(revision) + count), root_hash

    monkeypatch.setattr(publication, "_identity", identity)
    with pytest.raises(publication.PublicationUnavailable, match="changed"):
        publication.inspect_publication(client)


def test_retention_uses_committed_inventory_after_local_generation(tmp_path):
    from test.test_publish_cvmfs_metadata import _fixture, _bump, _git

    _, seed, _, _, _ = _fixture(tmp_path)
    _bump(seed, "20261010")
    subprocess.run(["python3", "cvmfs/generate_metadata.py"], cwd=seed, check=True)
    assert retained_ids(seed, "HEAD") == frozenset(
        {"tool_1.0_20250628", "tool_1.0_20261010"}
    )
    subprocess.run(["python3", "cvmfs/generate_metadata.py"], cwd=seed, check=True)
    assert retained_ids(seed, "HEAD") == frozenset(
        {"tool_1.0_20250628", "tool_1.0_20261010"}
    )
    _git(seed, "rm", "-f", "cvmfs/log.txt")
    _git(seed, "commit", "-m", "invalid inventory")
    with pytest.raises(subprocess.CalledProcessError):
        retained_ids(seed, "HEAD")


def test_client_cancellation_cleans_mount_and_process(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT_CANCEL", "1")
    with pytest.raises(publication.PublicationUnavailable, match="cancelled"):
        publication.inspect_publication(client)
    assert not Path((tmp_path / "root").read_text()).exists()


@pytest.mark.parametrize("client_fails,expected", [(False, 3), (True, 2)])
def test_cli_reports_pending_or_error_with_actions_summary(
    client, tmp_path, monkeypatch, client_fails, expected
):
    if client_fails:
        monkeypatch.setenv("CLIENT_FAIL", "1")
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    result = subprocess.run(
        [
            "python3",
            "-c",
            "from pathlib import Path; from cvmfs import check_publication as p; "
            "p.os.path.ismount = lambda root: (Path(root)/'mounted').exists(); "
            "raise SystemExit(p.main())",
            "--log",
            str(client),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert result.stdout.strip() in summary.read_text()
    if client_fails:
        assert "Cannot establish public CVMFS readiness" in result.stdout
    else:
        assert "revision 123" in result.stdout
        assert "Missing public container: missing" in result.stdout


def test_client_exit_racing_cleanup_does_not_invalidate_snapshot(client, monkeypatch):
    kill = os.killpg

    def exited(pid, signum):
        kill(pid, signum)
        raise ProcessLookupError

    monkeypatch.setattr(publication.os, "killpg", exited)
    assert publication.inspect_publication(client).missing == ("missing",)
