import base64
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "scripts" / "sync_neurocontainer_icons.py"

spec = importlib.util.spec_from_file_location("sync_neurocontainer_icons", SCRIPT)
sync_neurocontainer_icons = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sync_neurocontainer_icons
spec.loader.exec_module(sync_neurocontainer_icons)


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


def test_sync_decodes_matching_recipe_icon(tmp_path):
    neurocontainers_path = tmp_path / "neurocontainers"
    icons_dir = tmp_path / "icons"
    apps_json_path = tmp_path / "apps.json"

    write_recipe(neurocontainers_path, "alpha", PNG_DATA_URI)
    write_apps_json(apps_json_path, "alpha")

    result = sync_neurocontainer_icons.sync_icons(
        neurocontainers_path=neurocontainers_path,
        icons_dir=icons_dir,
        apps_json_path=apps_json_path,
    )

    assert result.matched_recipes == 1
    assert result.icons_found == 1
    assert result.changed_icons == [icons_dir / "alpha.png"]
    assert result.written_icons == [icons_dir / "alpha.png"]
    assert (icons_dir / "alpha.png").read_bytes() == PNG_BYTES


def test_sync_ignores_missing_unsupported_and_unmanaged_icons(tmp_path):
    neurocontainers_path = tmp_path / "neurocontainers"
    icons_dir = tmp_path / "icons"
    apps_json_path = tmp_path / "apps.json"

    write_recipe(neurocontainers_path, "missing-icon")
    write_recipe(neurocontainers_path, "unsupported-icon", "data:image/svg+xml;base64,PHN2Zy8+")
    write_recipe(neurocontainers_path, "unmanaged", PNG_DATA_URI)
    write_apps_json(apps_json_path, "missing-icon", "unsupported-icon")

    result = sync_neurocontainer_icons.sync_icons(
        neurocontainers_path=neurocontainers_path,
        icons_dir=icons_dir,
        apps_json_path=apps_json_path,
    )

    assert result.matched_recipes == 2
    assert result.icons_found == 1
    assert result.unsupported_icons == [
        neurocontainers_path / "recipes" / "unsupported-icon" / "build.yaml"
    ]
    assert not (icons_dir / "missing-icon.png").exists()
    assert not (icons_dir / "unsupported-icon.png").exists()
    assert not (icons_dir / "unmanaged.png").exists()


def test_sync_preserves_manual_icons(tmp_path):
    neurocontainers_path = tmp_path / "neurocontainers"
    icons_dir = tmp_path / "icons"
    apps_json_path = tmp_path / "apps.json"
    icons_dir.mkdir()
    manual_icon = icons_dir / "manual.png"
    manual_icon.write_bytes(b"manual icon content")
    existing_recipe_icon = icons_dir / "alpha.png"
    existing_recipe_icon.write_bytes(b"existing alpha icon")

    write_recipe(neurocontainers_path, "alpha", PNG_DATA_URI)
    write_apps_json(apps_json_path, "alpha")

    result = sync_neurocontainer_icons.sync_icons(
        neurocontainers_path=neurocontainers_path,
        icons_dir=icons_dir,
        apps_json_path=apps_json_path,
    )

    assert manual_icon.read_bytes() == b"manual icon content"
    assert existing_recipe_icon.read_bytes() == b"existing alpha icon"
    assert result.changed_icons == []


def test_check_mode_ignores_existing_icon_drift(tmp_path):
    neurocontainers_path = tmp_path / "neurocontainers"
    icons_dir = tmp_path / "icons"
    apps_json_path = tmp_path / "apps.json"
    icons_dir.mkdir()
    (icons_dir / "alpha.png").write_bytes(b"existing alpha icon")

    write_recipe(neurocontainers_path, "alpha", PNG_DATA_URI)
    write_apps_json(apps_json_path, "alpha")

    exit_code = sync_neurocontainer_icons.main(
        [
            "--neurocontainers-path",
            str(neurocontainers_path),
            "--icons-dir",
            str(icons_dir),
            "--apps-json",
            str(apps_json_path),
            "--check",
        ]
    )

    assert exit_code == 0
    assert (icons_dir / "alpha.png").read_bytes() == b"existing alpha icon"


def test_check_mode_reports_drift_without_writing(tmp_path):
    neurocontainers_path = tmp_path / "neurocontainers"
    icons_dir = tmp_path / "icons"
    apps_json_path = tmp_path / "apps.json"

    write_recipe(neurocontainers_path, "alpha", PNG_DATA_URI)
    write_apps_json(apps_json_path, "alpha")

    exit_code = sync_neurocontainer_icons.main(
        [
            "--neurocontainers-path",
            str(neurocontainers_path),
            "--icons-dir",
            str(icons_dir),
            "--apps-json",
            str(apps_json_path),
            "--check",
        ]
    )

    assert exit_code == 1
    assert not (icons_dir / "alpha.png").exists()
