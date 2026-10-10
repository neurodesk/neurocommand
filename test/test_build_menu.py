import configparser
import json
import xml.etree.ElementTree as et

import pytest

from neurodesk.build_menu import add_menu, apps_from_json, copy_runtime_tree

def menu_install(tmp_path):
    (tmp_path / "icons").mkdir()
    (tmp_path / "desktop-directories/apps").mkdir(parents=True)
    menu = tmp_path / "neurodesk-applications.menu"
    menu.write_text(
        '<Menu><Name>Applications</Name><Menu><Name>Neurodesk</Name>'
        '<Menu><Name>All Applications</Name><Include><And><Category>all-applications</Category>'
        '</And></Include><Directory>neurodesk/all-applications.directory</Directory>'
        '<Layout><DefaultLayout/></Layout></Menu></Menu></Menu>'
    )
    return menu


def test_submenu_uses_category_when_xml_children_are_reordered(tmp_path):
    menu = menu_install(tmp_path)
    add_menu(tmp_path, "Tool", "All Applications")
    root = et.parse(menu).getroot()
    assert root.findtext("Menu/Menu/Menu/Name") == "Tool"
    assert root.findtext("Menu/Menu/Menu/Include/And/Category") == "Tool"


@pytest.mark.parametrize("cli", [False, True])
def test_json_visibility_keeps_launchers_and_visible_submenus(tmp_path, cli):
    menu = menu_install(tmp_path)
    apps = tmp_path / "apps.json"
    apps.write_text(json.dumps({
        "tools": {"show_in_menu": False, "apps": {
            "hidden 1.0": {},
            "shown 1.0": {"show_in_menu": True, "terminal": False},
        }},
        "private": {"show_in_menu": False, "apps": {"private 1.0": {}}},
    }))
    apps_from_json(cli, "cli" if cli else "lxde", tmp_path, apps)
    assert sorted(path.name for path in (tmp_path / "bin").iterdir()) == [
        "hidden-1_0.sh", "private-1_0.sh", "shown-1_0.sh"
    ]
    if cli:
        assert not (tmp_path / "applications").exists()
        assert et.parse(menu).findall("Menu/Menu/Menu") == []
    else:
        assert [path.name for path in (tmp_path / "applications").iterdir()] == ["shown-1_0.desktop"]
        assert [node.findtext("Name") for node in et.parse(menu).findall("Menu/Menu/Menu")] == ["Tools"]
        desktop = configparser.ConfigParser()
        desktop.read(tmp_path / "applications/shown-1_0.desktop")
        assert desktop["Desktop Entry"]["Terminal"] == "false"


def test_runtime_copy_merges_dereferenced_links_and_preserves_file_metadata(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    destination.chmod(0o700)
    original = source / "executable"
    original.write_text("content")
    original.chmod(0o751)
    (source / "alias").symlink_to("executable")
    subdirectory = source / "directory"
    subdirectory.mkdir()
    (subdirectory / "nested").write_text("nested")
    (source / "directory-alias").symlink_to("directory", target_is_directory=True)
    (source / ".nfs-stale").write_text("temporary")
    (destination / "site-file").write_text("keep")
    copy_runtime_tree(source, destination)
    assert (destination / "site-file").read_text() == "keep"
    assert (destination / "alias").read_text() == "content"
    assert not (destination / "alias").is_symlink()
    assert not (destination / "directory-alias").is_symlink()
    assert (destination / "directory-alias/nested").read_text() == "nested"
    assert (destination / "alias").stat().st_mode & 0o777 == 0o751
    assert (destination / "alias").stat().st_mtime_ns == original.stat().st_mtime_ns
    assert destination.stat().st_mode & 0o777 == 0o700
    assert not (destination / ".nfs-stale").exists()


def test_bundle_adds_launcher_and_menu_entry_from_manifest(tmp_path):
    menu = menu_install(tmp_path)
    apps = tmp_path / "apps.json"
    apps.write_text(json.dumps({"tool": {"apps": {"tool 1.0": {"version": "20261001"}}}}))
    (tmp_path / "bundles.json").write_text(json.dumps({
        "schema_version": 1,
        "bundles": [{
            "name": "suite", "version": "2026.10", "description": "Suite",
            "categories": ["all applications"],
            "dependencies": [{"name": "tool", "version": "1.0"}],
        }],
    }))
    apps_from_json(False, "lxde", tmp_path, apps)
    assert (tmp_path / "bin/suite-2026_10.sh").read_text().endswith('suite 2026.10 "$@"\n')
    desktop = configparser.ConfigParser()
    desktop.read(tmp_path / "applications/suite-2026_10.desktop")
    assert desktop["Desktop Entry"]["Categories"] == "suite"
    assert any(node.findtext("Name") == "Suite" for node in et.parse(menu).findall("Menu/Menu/Menu"))
