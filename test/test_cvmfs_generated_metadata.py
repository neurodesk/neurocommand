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


def test_stratum_sync_publishes_log_and_applist_together():
    script = SYNC_SCRIPT.read_text()

    assert 'python3 "$repo_path/cvmfs/json_gen.py"' in script
    assert 'local generated_rel_paths=("cvmfs/log.txt" "cvmfs/applist.json")' in script
    assert 'git -C "$repo_path" add "${generated_rel_paths[@]}"' in script


def test_stratum_sync_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(SYNC_SCRIPT)], check=True)
