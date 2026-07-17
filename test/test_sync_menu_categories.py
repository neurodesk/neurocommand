import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "workflows" / "scripts" / "sync_menu_categories.py"

spec = importlib.util.spec_from_file_location("sync_menu_categories", SCRIPT)
sync_menu_categories = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sync_menu_categories
spec.loader.exec_module(sync_menu_categories)


MENU = """<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"
 "http://www.freedesktop.org/standards/menu-spec/1.0/menu.dtd">
<Menu>
\t<Name>Applications</Name>
\t<Menu>
\t\t<Name>Neurodesk</Name>
\t\t<Menu>
\t\t\t<Name>Existing Imaging</Name>
\t\t\t<Directory>neurodesk/existing-imaging.directory</Directory>
\t\t\t<Include><And><Category>existing-imaging</Category></And></Include>
\t\t</Menu>
\t</Menu> <!-- End Neurodesk -->
</Menu>
"""

BUILD_MENU = """def build_menu(installdir):
    if installdir:
        write_directory_file("Existing Imaging", directories_path, icon_dir)

    appsjson = Path("neurodesk/apps.json")
"""

ICON_TEST = """DIRECTORY_NAMES = [
    "Existing Imaging",
]
"""


def make_fixture(tmp_path):
    apps_json = tmp_path / "apps.json"
    apps_json.write_text(
        json.dumps(
            {
                "existing": {"apps": {}, "categories": ["existing imaging"]},
                "new": {"apps": {}, "categories": ["fetal imaging"]},
            }
        )
    )
    menu = tmp_path / "neurodesk-applications.menu"
    menu.write_text(MENU)
    build_menu = tmp_path / "build_menu.py"
    build_menu.write_text(BUILD_MENU)
    icon_test = tmp_path / "test_icon_coverage.py"
    icon_test.write_text(ICON_TEST)
    icons = tmp_path / "icons"
    icons.mkdir()
    (icons / "existing.png").write_bytes(b"existing")
    (icons / "neurodesk.png").write_bytes(b"fallback")
    return apps_json, menu, build_menu, icon_test, icons


def run_sync(paths, *, check=False):
    apps_json, menu, build_menu, icon_test, icons = paths
    return sync_menu_categories.sync_categories(
        apps_json_path=apps_json,
        menu_path=menu,
        build_menu_path=build_menu,
        icon_test_path=icon_test,
        icons_dir=icons,
        check=check,
    )


def test_sync_adds_new_category_to_every_required_location(tmp_path):
    paths = make_fixture(tmp_path)

    result = run_sync(paths)

    _, menu, build_menu, icon_test, icons = paths
    assert result.missing_menu_categories == ["fetal-imaging"]
    assert result.missing_build_directories == ["Fetal Imaging"]
    assert result.missing_icon_test_directories == ["Fetal Imaging"]
    assert result.missing_icons == [icons / "fetal.png"]
    assert set(result.changed_files) == {
        menu,
        build_menu,
        icon_test,
        icons / "fetal.png",
    }
    assert "<Name>Fetal Imaging</Name>" in menu.read_text()
    assert "<Category>fetal-imaging</Category>" in menu.read_text()
    assert (
        'write_directory_file("Fetal Imaging", directories_path, icon_dir)'
        in build_menu.read_text()
    )
    assert '    "Fetal Imaging",' in icon_test.read_text()
    assert (icons / "fetal.png").read_bytes() == b"fallback"


def test_sync_is_idempotent(tmp_path):
    paths = make_fixture(tmp_path)
    run_sync(paths)

    result = run_sync(paths)

    assert not result.changes_needed
    assert result.changed_files == []


def test_check_reports_drift_without_writing(tmp_path):
    paths = make_fixture(tmp_path)
    original_contents = [path.read_bytes() for path in paths[1:4]]

    result = run_sync(paths, check=True)

    assert result.changes_needed
    assert [path.read_bytes() for path in paths[1:4]] == original_contents
    assert not (paths[-1] / "fetal.png").exists()
