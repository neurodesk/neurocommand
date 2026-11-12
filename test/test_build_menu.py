import configparser
import json
import os
import subprocess
import xml.etree.ElementTree as et

import pytest

from neurodesk.build_menu import add_menu, apps_from_json, copy_runtime_tree


def menu_install(tmp_path):
    (tmp_path / "icons").mkdir()
    (tmp_path / "desktop-directories/apps").mkdir(parents=True)
    menu = tmp_path / "neurodesk-applications.menu"
    menu.write_text(
        "<Menu><Name>Applications</Name><Menu><Name>Neurodesk</Name>"
        "<Menu><Name>All Applications</Name><Include><And><Category>all-applications</Category>"
        "</And></Include><Directory>neurodesk/all-applications.directory</Directory>"
        "<Layout><DefaultLayout/></Layout></Menu></Menu></Menu>"
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
    apps.write_text(
        json.dumps(
            {
                "tools": {
                    "show_in_menu": False,
                    "apps": {
                        "hidden 1.0": {},
                        "shown 1.0": {"show_in_menu": True, "terminal": False},
                    },
                },
                "private": {"show_in_menu": False, "apps": {"private 1.0": {}}},
            }
        )
    )
    apps_from_json(cli, "cli" if cli else "lxde", tmp_path, apps)
    assert sorted(path.name for path in (tmp_path / "bin").iterdir()) == [
        "hidden-1_0.sh",
        "private-1_0.sh",
        "shown-1_0.sh",
    ]
    if cli:
        assert not (tmp_path / "applications").exists()
        assert et.parse(menu).findall("Menu/Menu/Menu") == []
    else:
        assert [path.name for path in (tmp_path / "applications").iterdir()] == [
            "shown-1_0.desktop"
        ]
        assert [
            node.findtext("Name") for node in et.parse(menu).findall("Menu/Menu/Menu")
        ] == ["Tools"]
        desktop = configparser.ConfigParser()
        desktop.read(tmp_path / "applications/shown-1_0.desktop")
        assert desktop["Desktop Entry"]["Terminal"] == "false"


@pytest.mark.parametrize(
    ("machine", "entries"),
    [
        ("x86_64", ["tool-1_0"]),
        ("aarch64", ["tool-1_0", "tool_arm64-1_0"]),
        ("arm64", ["tool-1_0", "tool_arm64-1_0"]),
    ],
)
def test_arm64_menu_entries_follow_host_architecture(
    tmp_path, monkeypatch, machine, entries
):
    monkeypatch.setenv("NEURODESK_ARCH", machine)
    menu = menu_install(tmp_path)
    for directory, stale in [
        ("applications", "tool_arm64-1_0.desktop"),
        ("bin", "tool_arm64-1_0.sh"),
    ]:
        (tmp_path / directory).mkdir()
        (tmp_path / directory / stale).write_text("stale")
    apps = tmp_path / "apps.json"
    apps.write_text(
        json.dumps(
            {
                "tool": {"apps": {"tool 1.0": {}}},
                "tool_arm64": {"apps": {"tool_arm64 1.0": {"show_in_menu": True}}},
            }
        )
    )
    apps_from_json(False, "lxde", tmp_path, apps)
    assert sorted(path.name for path in (tmp_path / "applications").iterdir()) == [
        f"{entry}.desktop" for entry in entries
    ]
    assert sorted(path.name for path in (tmp_path / "bin").iterdir()) == [
        f"{entry}.sh" for entry in entries
    ]
    assert [
        node.findtext("Name") for node in et.parse(menu).findall("Menu/Menu/Menu")
    ] == ["Tool"]


def desktop_entry(installdir, name):
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(installdir / "applications" / name)
    return desktop["Desktop Entry"]


def test_arm64_entries_are_labelled_inside_the_x86_64_submenu(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURODESK_ARCH", "aarch64")
    menu_install(tmp_path)
    apps = tmp_path / "apps.json"
    apps.write_text(
        json.dumps(
            {
                "tool_arm64": {
                    "apps": {
                        "tool_arm64 1.0": {},
                        "viewerGUI-tool_arm64 1.0": {"exec": "viewer"},
                    }
                },
            }
        )
    )
    apps_from_json(False, "lxde", tmp_path, apps)

    cli_entry = desktop_entry(tmp_path, "tool_arm64-1_0.desktop")
    assert cli_entry["Name"] == "tool 1.0 arm64"
    assert cli_entry["Comment"] == "tool_arm64 1.0"
    assert cli_entry["Categories"] == "tool"
    assert cli_entry["Exec"].endswith("tool_arm64-1_0.sh")
    gui_entry = desktop_entry(tmp_path, "viewergui-tool_arm64-1_0.desktop")
    assert gui_entry["Name"] == "viewerGUI 1.0 arm64"


@pytest.mark.parametrize(
    ("machine", "comment"),
    [
        ("x86_64", "tool 1.0"),
        ("aarch64", "tool 1.0 (x86_64 build, runs under emulation)"),
    ],
)
def test_x86_64_entries_run_the_x86_64_build_on_arm64(
    tmp_path, monkeypatch, machine, comment
):
    monkeypatch.setenv("NEURODESK_ARCH", machine)
    menu_install(tmp_path)
    apps = tmp_path / "apps.json"
    apps.write_text(
        json.dumps(
            {
                "tool": {"apps": {"tool 1.0": {}}},
                "tool_arm64": {"apps": {"tool_arm64 1.0": {}}},
            }
        )
    )
    # sh_prefix may be a command that does not accept variable assignments
    apps_from_json(False, "lxde", tmp_path, apps, sh_prefix="nice -n 10")
    entry = desktop_entry(tmp_path, "tool-1_0.desktop")
    assert entry["Name"] == "tool 1.0"
    assert entry["Comment"] == comment
    fetch_and_run = tmp_path / "fetch_and_run.sh"
    fetch_and_run.write_text('#!/bin/bash\necho "${NEURODESK_ARCH:-unset} $*"\n')
    fetch_and_run.chmod(0o755)
    launched = subprocess.run(
        ["bash", str(tmp_path / "bin/tool-1_0.sh"), "--flag"],
        env={k: v for k, v in os.environ.items() if k != "NEURODESK_ARCH"},
        capture_output=True,
        text=True,
        check=True,
    )
    arch = "x86_64" if machine == "aarch64" else "unset"
    assert launched.stdout == f"{arch} tool 1.0 --flag\n"
    if machine == "aarch64":
        native = (tmp_path / "bin/tool_arm64-1_0.sh").read_text()
        assert "NEURODESK_ARCH" not in native
        assert (
            "emulation"
            not in (tmp_path / "applications/tool_arm64-1_0.desktop").read_text()
        )


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


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_bundle_adds_launcher_and_menu_entry_from_manifest(
    tmp_path, monkeypatch, machine
):
    monkeypatch.setenv("NEURODESK_ARCH", machine)
    menu = menu_install(tmp_path)
    apps = tmp_path / "apps.json"
    apps.write_text(
        json.dumps({"tool": {"apps": {"tool 1.0": {"version": "20261001"}}}})
    )
    (tmp_path / "bundles.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bundles": [
                    {
                        "name": "suite",
                        "version": "2026.10",
                        "description": "Suite",
                        "categories": ["all applications"],
                        "dependencies": [{"name": "tool", "version": "1.0"}],
                    }
                ],
            }
        )
    )
    apps_from_json(False, "lxde", tmp_path, apps)
    launcher = (tmp_path / "bin/suite-2026_10.sh").read_text()
    assert launcher.endswith('suite 2026.10 "$@"\n')
    # the bundle's host shell must keep picking native builds on arm64
    assert "NEURODESK_ARCH" not in launcher
    desktop = configparser.ConfigParser()
    desktop.read(tmp_path / "applications/suite-2026_10.desktop")
    assert desktop["Desktop Entry"]["Categories"] == "suite"
    assert desktop["Desktop Entry"]["Comment"] == "suite 2026.10"
    assert any(
        node.findtext("Name") == "Suite"
        for node in et.parse(menu).findall("Menu/Menu/Menu")
    )
