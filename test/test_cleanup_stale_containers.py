import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "scripts" / "cleanup_stale_containers.py"

spec = importlib.util.spec_from_file_location("cleanup_stale_containers", SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def test_release_metadata_protects_named_and_legacy_images(tmp_path):
    releases = tmp_path / "releases"

    named = releases / "workshopdemo_arm64" / "1.0.0.json"
    named.parent.mkdir(parents=True)
    named.write_text(json.dumps({"apps": {"workshopdemo_arm64 1.0.0": {"version": "20260721"}}}))

    legacy = releases / "amico" / "2.1.0-arm64.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "apps": {
                    "amico 2.1.0 arm64": {
                        "version": "20260512",
                        "image": "amico_2.1.0_arm64",
                    }
                }
            }
        )
    )

    assert cleanup.load_release_keys(releases) == {
        "workshopdemo_arm64_1.0.0_20260721.simg",
        "amico_2.1.0_arm64_20260512.simg",
    }
