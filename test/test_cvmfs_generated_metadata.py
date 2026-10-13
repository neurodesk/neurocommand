import json
from pathlib import Path
import subprocess

from cvmfs import json_gen


ROOT = Path(__file__).resolve().parents[1]
APPS_JSON = ROOT / "neurodesk" / "apps.json"
LOG = ROOT / "cvmfs" / "log.txt"
APPLIST = ROOT / "cvmfs" / "applist.json"
SYNC_SCRIPT = ROOT / "cvmfs" / "sync_containers_to_cvmfs.sh"
BINFMT_SCRIPT = ROOT / "cvmfs" / "ensure_binfmt.sh"


def test_checked_in_applist_matches_current_log(tmp_path):
    generated_applist = tmp_path / "applist.json"

    json_gen.process_text_to_json(
        log_path=LOG,
        output_path=generated_applist,
        apps_json_path=APPS_JSON,
    )

    assert json.loads(APPLIST.read_text()) == json.loads(generated_applist.read_text())


def test_hidden_app_remains_hidden_after_build_date_changes(tmp_path):
    apps_json = tmp_path / "apps.json"
    log = tmp_path / "log.txt"
    applist = tmp_path / "applist.json"

    apps_json.write_text(
        json.dumps(
            {
                "neurodesktop-lite": {
                    "show_in_applist": False,
                    "apps": {
                        "neurodesktop-lite 20260428": {
                            "version": "20260813",
                        }
                    },
                }
            }
        )
    )
    log.write_text(
        "neurodesktop-lite_20260428_20260808 categories:programming,\n"
        "visible-app_1.0_20260808 categories:visualization,\n"
    )

    json_gen.process_text_to_json(
        log_path=log,
        output_path=applist,
        apps_json_path=apps_json,
    )

    assert json.loads(applist.read_text()) == {
        "list": [
            {
                "application": "visible-app_1.0_20260808",
                "categories": ["visualization"],
            }
        ]
    }


def _shell_function(script, name):
    start = script.index(f"{name}() {{")
    return script[start : script.index("\n}\n", start) + 3]


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_stratum_sync_mirrors_origin_and_discards_rejected_metadata_commits(
    tmp_path,
):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(seed))
    (seed / "log.txt").write_text("old_1.0_20250628 categories:\n")
    _git(seed, "add", "log.txt")
    _git(seed, "commit", "-m", "published")
    _git(seed, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(checkout))
    # A metadata commit whose push branch protection rejected.
    (checkout / "log.txt").write_text("local_1.0_20261010 categories:\n")
    _git(checkout, "commit", "-am", "Regenerate CVMFS metadata from apps.json")
    (seed / "log.txt").write_text("new_1.0_20261010 categories:\n")
    _git(seed, "commit", "-am", "publish uploaded metadata")
    _git(seed, "push", "origin", "HEAD:main")

    function = _shell_function(SYNC_SCRIPT.read_text(), "sync_checkout_to_origin")
    result = subprocess.run(
        ["bash", "-c", f'{function}\nsync_checkout_to_origin "$1"', "_", checkout],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Regenerate CVMFS metadata from apps.json" in result.stdout
    assert _git(checkout, "rev-parse", "HEAD") == _git(seed, "rev-parse", "HEAD")
    assert (checkout / "log.txt").read_text() == "new_1.0_20261010 categories:\n"


def test_stratum_sync_never_pushes_metadata():
    assert "push" not in SYNC_SCRIPT.read_text()


def test_stratum_sync_uses_tested_retrieval_scripts_without_nectar_gate():
    script = SYNC_SCRIPT.read_text()

    assert (
        'cp -a "$NEUROCOMMAND_LOCAL_REPO/neurodesk/transparent-singularity/."' in script
    )
    assert (
        "git clone https://github.com/NeuroDesk/transparent-singularity" not in script
    )
    assert "object-store.rc.nectar.org.au" not in script


def test_stratum_sync_guards_container_directory_changes():
    script = SYNC_SCRIPT.read_text()

    assert "if ! cd /cvmfs/neurodesk.ardc.edu.au/containers/; then" in script
    assert 'if ! cd "$IMAGENAME_BUILDDATE"; then' in script
    assert script.count("abort_cvmfs_transaction neurodesk.ardc.edu.au") >= 4


def test_stratum_sync_checks_binfmt_before_opening_transaction():
    script = SYNC_SCRIPT.read_text()

    check = '"$NEUROCOMMAND_LOCAL_REPO/cvmfs/ensure_binfmt.sh" "$IMAGENAME_BUILDDATE"'
    transaction = "open_cvmfs_transaction neurodesk.ardc.edu.au"
    loop = script[script.index("while IFS= read -r LINE") :]

    assert check in loop
    assert loop.index(check) < loop.index(transaction)


def test_binfmt_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(BINFMT_SCRIPT)], check=True)


def test_stratum_sync_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(SYNC_SCRIPT)], check=True)
