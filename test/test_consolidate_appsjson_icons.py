import base64
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


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()


def write_recipe(neurocontainers_path, name, icon_value=None):
    recipe_path = neurocontainers_path / "recipes" / name
    recipe_path.mkdir(parents=True)
    lines = [f"name: {name}"]
    if icon_value is not None:
        lines.append(f"icon: {icon_value}")
    (recipe_path / "build.yaml").write_text("\n".join(lines) + "\n")


def write_apps_json(path, *app_names):
    path.write_text(json.dumps({name: {"apps": {}} for name in app_names}))


def test_sync_consolidated_icons_writes_and_returns_new_icons(tmp_path):
    neurocontainers_path = tmp_path / "neurocontainers"
    icons_dir = tmp_path / "icons"
    apps_json_path = tmp_path / "apps.json"

    write_recipe(neurocontainers_path, "alpha", PNG_DATA_URI)
    write_apps_json(apps_json_path, "alpha")

    written = consolidate_appsjson_queue.sync_consolidated_icons(
        neurocontainers_path,
        icons_dir,
        str(apps_json_path),
    )

    assert written == [str(icons_dir / "alpha.png")]
    assert (icons_dir / "alpha.png").read_bytes() == PNG_BYTES


def test_sync_consolidated_icons_skips_when_neurocontainers_missing(tmp_path):
    written = consolidate_appsjson_queue.sync_consolidated_icons(
        tmp_path / "absent",
        tmp_path / "icons",
        str(tmp_path / "apps.json"),
    )

    assert written == []


def test_sync_consolidated_icons_never_raises_on_bad_icon(tmp_path):
    neurocontainers_path = tmp_path / "neurocontainers"
    icons_dir = tmp_path / "icons"
    apps_json_path = tmp_path / "apps.json"

    # Declared as PNG but the payload is not valid base64, which sync_icons
    # raises on. Consolidation must not be blocked by an upstream data error.
    write_recipe(neurocontainers_path, "alpha", "data:image/png;base64,not-valid!!")
    write_apps_json(apps_json_path, "alpha")

    written = consolidate_appsjson_queue.sync_consolidated_icons(
        neurocontainers_path,
        icons_dir,
        str(apps_json_path),
    )

    assert written == []
    assert not (icons_dir / "alpha.png").exists()
