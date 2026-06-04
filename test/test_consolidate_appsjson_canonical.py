import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "workflows" / "scripts"
SCRIPT = SCRIPTS / "consolidate_appsjson_queue.py"

# The consolidation script imports sync_neurocontainer_icons from its own
# directory, so make that importable before loading it.
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("consolidate_appsjson_queue", SCRIPT)
consolidate_appsjson_queue = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = consolidate_appsjson_queue
spec.loader.exec_module(consolidate_appsjson_queue)


def _sample_payload():
    # Top-level keys deliberately out of order, with a tool appended last as the
    # consolidation bot does for newly added tools. Nested fields use the
    # generator's order (version, exec, apptainer_args -- NOT alphabetical).
    return {
        "blender": {
            "apps": {"blender 5.0.1": {"version": "20260101", "exec": ""}},
            "categories": ["image segmentation"],
        },
        "afni": {
            "apps": {"afni 1.0": {"version": "20260101", "exec": "", "apptainer_args": []}},
            "categories": ["functional imaging"],
        },
        # appended out of alphabetical order, as a freshly consolidated tool
        "blastct": {
            "apps": {"blastct 2.0.0": {"version": "20260512", "exec": "", "apptainer_args": []}},
            "categories": ["structural imaging"],
        },
    }


def test_sort_top_level_keys_sorts_only_top_level():
    ordered = consolidate_appsjson_queue.sort_top_level_keys(_sample_payload())

    # Top-level tool names alphabetised...
    assert list(ordered) == ["afni", "blastct", "blender"]
    # ...but nested field order is preserved (generator does not sort these).
    assert list(ordered["afni"]["apps"]["afni 1.0"]) == ["version", "exec", "apptainer_args"]
    assert list(ordered["blastct"]) == ["apps", "categories"]


def test_write_json_matches_generator_byte_form(tmp_path):
    out = tmp_path / "apps.json"
    payload = _sample_payload()
    consolidate_appsjson_queue.write_json(str(out), payload)

    text = out.read_text()
    # Byte-identical to the neurocontainers generator's serialization: top-level
    # sorted, indent=4, default separators, NO trailing newline.
    expected = json.dumps({k: payload[k] for k in sorted(payload)}, indent=4)
    assert text == expected
    assert not text.endswith("\n")


def test_write_json_is_idempotent(tmp_path):
    out = tmp_path / "apps.json"
    consolidate_appsjson_queue.write_json(str(out), _sample_payload())
    first = out.read_text()
    # Re-writing the already-canonical payload must not change a single byte.
    consolidate_appsjson_queue.write_json(str(out), json.loads(first))
    assert out.read_text() == first


def test_render_diff_ignores_pure_reordering():
    base = _sample_payload()
    # Same content, different top-level insertion order -> canonicalisation must
    # collapse it to an empty diff (no phantom reorder churn in the PR body).
    reordered = {k: base[k] for k in reversed(list(base))}
    diff = consolidate_appsjson_queue.render_appsjson_diff("neurodesk/apps.json", base, reordered)
    assert diff == ""


def test_render_diff_shows_real_change_only():
    base = _sample_payload()
    changed = json.loads(json.dumps(base))
    changed["afni"]["apps"]["afni 1.0"]["version"] = "20260601"
    diff = consolidate_appsjson_queue.render_appsjson_diff("neurodesk/apps.json", base, changed)

    added = [line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    assert any('"version": "20260601"' in line for line in added)
    assert any('"version": "20260101"' in line for line in removed)
    # Only the single version line changed on each side.
    assert len(added) == 1 and len(removed) == 1
