import json
from pathlib import Path
import subprocess

from cvmfs import json_gen


ROOT = Path(__file__).resolve().parents[1]
APPS_JSON = ROOT / "neurodesk" / "apps.json"
LOG = ROOT / "cvmfs" / "log.txt"
APPLIST = ROOT / "cvmfs" / "applist.json"
SYNC_SCRIPT = ROOT / "cvmfs" / "sync_containers_to_cvmfs.sh"


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


def test_stratum_sync_publishes_log_and_applist_together():
    script = SYNC_SCRIPT.read_text()

    assert 'python3 "$repo_path/cvmfs/json_gen.py"' in script
    assert 'local generated_rel_paths=("cvmfs/log.txt" "cvmfs/applist.json")' in script
    assert 'git -C "$repo_path" add "${generated_rel_paths[@]}"' in script


def test_stratum_sync_uses_tested_retrieval_scripts_without_nectar_gate():
    script = SYNC_SCRIPT.read_text()

    assert 'cp -a "$NEUROCOMMAND_LOCAL_REPO/neurodesk/transparent-singularity/."' in script
    assert "git clone https://github.com/NeuroDesk/transparent-singularity" not in script
    assert "object-store.rc.nectar.org.au" not in script


def test_stratum_sync_guards_container_directory_changes():
    script = SYNC_SCRIPT.read_text()

    assert "if ! cd /cvmfs/neurodesk.ardc.edu.au/containers/; then" in script
    assert 'if ! cd "$IMAGENAME_BUILDDATE"; then' in script
    assert script.count("abort_cvmfs_transaction neurodesk.ardc.edu.au") >= 4


def test_stratum_sync_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(SYNC_SCRIPT)], check=True)
