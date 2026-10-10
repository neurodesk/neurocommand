"""Generate the menu items."""

import configparser
import json
import os
import sys
from pathlib import Path
import re
from typing import Callable, Optional, Text, TextIO
import xml.etree.ElementTree as et
from xml.dom import minidom
import shutil
import shlex
import logging
from dataclasses import dataclass
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parent / "transparent-singularity"))
from artifact_renderer import load_bundles, bundle_menu_entries, publish_bundles

APP_MENU_KWARGS = {"version", "terminal", "apptainer_args"}

# MIME types claimed by document-editing executables, keyed by the app's exec
# name. Entries here get a MimeType= declaration and a %F field code in their
# .desktop file so file managers can open documents with them via double-click.
# text/csv is deliberately not claimed so csv data files keep opening in a
# text editor.
EXEC_MIMETYPES = {
    "lowriter": [
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.text-template",
        "application/vnd.oasis.opendocument.text-master",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "application/rtf",
    ],
    "localc": [
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.spreadsheet-template",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    ],
    "loimpress": [
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.presentation-template",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.template",
    ],
    "lodraw": [
        "application/vnd.oasis.opendocument.graphics",
        "application/vnd.oasis.opendocument.graphics-template",
        "application/vnd.visio",
    ],
    "lobase": [
        "application/vnd.oasis.opendocument.database",
        "application/vnd.sun.xml.base",
    ],
    "lomath": [
        "application/vnd.oasis.opendocument.formula",
    ],
}


def chmod_if_new(path: Path, mode: int, existed_before: bool) -> None:
    """Set file mode only when this run created the target file."""
    if not existed_before:
        os.chmod(path, mode)


def _stat_mode(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    return path.stat().st_mode & 0o777


def _restore_mode(
    path: Path, existed_before: bool, was_recreated: bool, previous_mode: Optional[int]
) -> None:
    """Preserve mode when replacing an existing non-writable file."""
    if existed_before and was_recreated and previous_mode is not None:
        os.chmod(path, previous_mode)


def writefile_with_mode(
    path: Path, writer: Callable[[TextIO], None], mode: Optional[int] = None
) -> None:
    """Write file content with fallback for non-writable existing files."""
    path_existed = path.exists()
    previous_mode = _stat_mode(path)
    was_recreated = False
    try:
        with open(path, "w") as fh:
            writer(fh)
    except PermissionError:
        if not path_existed:
            raise
        path.unlink()
        was_recreated = True
        with open(path, "w") as fh:
            writer(fh)
    if mode is not None:
        chmod_if_new(path, mode, path_existed)
    _restore_mode(path, path_existed, was_recreated, previous_mode)


def copyfile_with_mode(src: Path, dest: Path, mode: Optional[int] = None) -> None:
    """Copy a file and optionally chmod only when destination is newly created."""
    dest_existed = dest.exists()
    previous_mode = _stat_mode(dest)
    was_recreated = False
    try:
        shutil.copyfile(src, dest)
    except PermissionError:
        # Existing files from prior sudo runs can be non-writable even when
        # the parent directory is writable. Recreate the file in that case.
        if not dest_existed:
            raise
        dest.unlink()
        was_recreated = True
        shutil.copyfile(src, dest)
    if mode is not None:
        chmod_if_new(dest, mode, dest_existed)
    _restore_mode(dest, dest_existed, was_recreated, previous_mode)


def write_directory_file(name, file_dir, icon_dir):
    logging.info(f"Adding submenu for '{name}'")
    file_path = file_dir / f"{name.lower().replace(' ', '-')}.directory"
    icon_path = icon_dir / f"{name.lower().split()[0]}.png"
    if name == "Neurodesk":
        icon_path = icon_dir / f"aedapt.png"
    icon_src = Path(__file__).parent / "icons" / icon_path.name
    try:
        copyfile_with_mode(icon_src, icon_path)
    except FileNotFoundError:
        logging.warning(f"{icon_src} not found")
        icon_src = Path(__file__).parent / "icons/neurodesk.png"
        copyfile_with_mode(icon_src, icon_path)

    # Generate `.directory` file
    entry = configparser.ConfigParser()
    entry.optionxform = str
    entry["Desktop Entry"] = {
        "Name": name,
        "Comment": name,
        "Icon": icon_path,
        "Type": "Directory",
    }
    file_dir.mkdir(exist_ok=True)

    def _write_directory(directory_file):
        entry.write(directory_file, space_around_delimiters=False)

    writefile_with_mode(file_path, _write_directory, mode=0o644)
    return file_path


def add_menu(installdir: Path, name: Text, category: Text) -> None:
    """Add a submenu to 'Neurodesk' menu.

    Parameters
    ----------
    name : Text
        The name of the submenu.
    """

    # Generate `.directory` file
    file_dir = installdir / "desktop-directories/apps"
    icon_dir = installdir / f"icons"
    file_path = write_directory_file(name, file_dir, icon_dir)

    # Add entry to `.menu` file
    menu_path = installdir / "neurodesk-applications.menu"
    with open(menu_path, "r") as xml_file:
        s = xml_file.read()
    s = re.sub(r"\s+(?=<)", "", s)
    root = et.fromstring(s)
    category_name = f"{category.lower().replace(' ', '-')}"
    for menu_el in root.findall(".//Menu/Menu"):
        if menu_el.findtext("Include/And/Category") == category_name:
            sub_el = et.SubElement(menu_el, "Menu")
            name_el = et.SubElement(sub_el, "Name")
            name_el.text = name.capitalize()
            dir_el = et.SubElement(sub_el, "Directory")
            dir_el.text = f"neurodesk/apps/{file_path.name}"
            include_el = et.SubElement(sub_el, "Include")
            and_el = et.SubElement(include_el, "And")
            cat_el = et.SubElement(and_el, "Category")
            cat_el.text = name.replace(" ", "-")
            xmlstr = minidom.parseString(et.tostring(root)).toprettyxml(indent="\t")

            def _write_menu(f):
                f.write('<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"\n ')
                f.write(
                    '"http://www.freedesktop.org/standards/menu-spec/1.0/menu.dtd">\n\n'
                )
                f.write(xmlstr[xmlstr.find("?>") + 3 :])

            writefile_with_mode(menu_path, _write_menu, mode=0o644)
            break


def app_menu_data(app_data: dict) -> dict:
    data = {key: app_data[key] for key in APP_MENU_KWARGS if key in app_data}
    data["command"] = app_data.get("exec", "")
    data["apptainer_args"] = tuple(data.get("apptainer_args") or ())
    return data


def visibility_flag(data: dict, name: Text, default: bool = True) -> bool:
    return data.get(name, default) is not False


@dataclass(frozen=True)
class NeurodeskApp:
    """Complete application specification, independent of artifact writes."""

    deskenv: str
    installdir: Path
    name: str
    sh_prefix: str = ""
    version: str = ""
    category: str = ""
    command: str = ""
    terminal: bool = True
    apptainer_args: tuple[str, ...] = ()
    launcher_command: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "apptainer_args", tuple(self.apptainer_args or ()))

    @property
    def basename(self) -> str:
        return self.name.lower().replace(" ", "-").replace(".", "_")

    @property
    def container_name(self) -> str:
        container_spec = self.name.split("-", 1)[1] if self.command else self.name
        return container_spec.rsplit(" ", 1)[0]

    @property
    def container_version(self) -> str:
        container_spec = self.name.split("-", 1)[1] if self.command else self.name
        return container_spec.rsplit(" ", 1)[1] if " " in container_spec else ""

    @property
    def display_name(self) -> str:
        if self.command:
            return f"{self.name.split('-', 1)[0]} {self.container_version}"
        return self.name

    @property
    def sh_path(self) -> Path:
        return self.installdir / "bin" / f"{self.basename}.sh"

    @property
    def desktop_path(self) -> Path:
        return self.installdir / "applications" / f"{self.basename}.desktop"

    @property
    def icon_path(self) -> Path:
        return self.installdir / "icons" / f"{self.name.split()[0]}.png"


def render_app_sh(app: NeurodeskApp) -> str:
    command = app.launcher_command
    if not command:
        arguments = [
            str(app.installdir / "fetch_and_run.sh"),
            app.container_name,
            app.container_version,
            *shlex.split(app.command),
        ]
        command = " ".join(shlex.quote(argument) for argument in arguments) + ' "$@"'
    return f"#!/usr/bin/env bash\n{app.sh_prefix} {command}\n"


def write_app_sh(app: NeurodeskApp) -> None:
    app.sh_path.parent.mkdir(exist_ok=True)
    content = render_app_sh(app)
    writefile_with_mode(app.sh_path, lambda output: output.write(content), mode=0o755)


def desktop_argument(value: str) -> str:
    """Quote reserved characters in a freedesktop Exec argument."""
    if not re.search(r'[\s"\\`$><~|&;*?#()\']', value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("`", "\\`").replace("$", "\\$")
    return '"' + escaped.replace("\\", "\\\\") + '"'


def render_app_menu(app: NeurodeskApp) -> str:
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    desktop = {
        "Name": app.display_name,
        "GenericName": app.display_name,
        "Comment": app.name,
        "Exec": f"/bin/bash {desktop_argument(str(app.sh_path))}",
        "Icon": str(app.icon_path),
        "Type": "Application",
        "Categories": app.category,
    }
    if app.deskenv == "mate":
        shell_command = f"/bin/bash {shlex.quote(str(app.sh_path))}"
        desktop["Exec"] = (
            f"mate-terminal --window --title {desktop_argument(app.name)} "
            f"-e {desktop_argument(shell_command)}"
        )
    else:
        desktop["Terminal"] = str(app.terminal).lower()
        mimetypes = EXEC_MIMETYPES.get(app.command)
        if mimetypes:
            desktop["Exec"] += " %F"
            desktop["MimeType"] = ";".join(mimetypes) + ";"
    entry["Desktop Entry"] = desktop
    output = StringIO()
    entry.write(output, space_around_delimiters=False)
    return output.getvalue()


def write_app_menu(app: NeurodeskApp) -> None:
    app.icon_path.parent.mkdir(exist_ok=True)
    icon_src = Path(__file__).parent / "icons" / app.icon_path.name
    try:
        copyfile_with_mode(icon_src, app.icon_path)
    except FileNotFoundError:
        logging.warning(f"{icon_src} not found")
        copyfile_with_mode(Path(__file__).parent / "icons/neurodesk.png", app.icon_path)
    app.desktop_path.parent.mkdir(exist_ok=True)
    content = render_app_menu(app)
    writefile_with_mode(
        app.desktop_path, lambda output: output.write(content), mode=0o644
    )


def apps_from_json(
    cli, deskenv: Text, installdir: Path, appsjson: Path, sh_prefix=""
) -> None:
    # Read applications file
    with open(appsjson, "r") as json_file:
        menu_entries = json.load(json_file)
    manifest = appsjson.parent / "bundles.json"
    if manifest.is_file():
        bundles = load_bundles(manifest, menu_entries)
        for name, bundle_group in bundle_menu_entries(bundles).items():
            group = menu_entries.setdefault(name, {"apps": {}, "categories": []})
            group["apps"].update(bundle_group["apps"])
            group["categories"] = list(
                dict.fromkeys(group.get("categories", []) + bundle_group["categories"])
            )

    for menu_name, menu_data in menu_entries.items():
        default_show_in_menu = visibility_flag(menu_data, "show_in_menu")
        apps = menu_data.get("apps", {})
        menu_apps = [
            app_data
            for app_data in apps.values()
            if visibility_flag(app_data, "show_in_menu", default_show_in_menu)
        ]
        # Add submenu
        if not cli and menu_apps:
            add_menu(installdir, menu_name, "all applications")
            for category in menu_data.get("categories") or []:
                add_menu(installdir, menu_name, category)
        for app_name, app_data in apps.items():
            show_in_menu = visibility_flag(
                app_data, "show_in_menu", default_show_in_menu
            )
            app = NeurodeskApp(
                deskenv=deskenv,
                installdir=installdir,
                sh_prefix=sh_prefix,
                name=app_name,
                category=menu_name.replace(" ", "-"),
                **app_menu_data(app_data),
            )
            write_app_sh(app)
            if not cli and show_in_menu:
                write_app_menu(app)


def neurodesk_xml(xml: Path, newxml: Path) -> None:
    oldtag = "<Menu>"
    newtag = "<MergeFile>neurodesk-applications.menu</MergeFile>"
    replace = True

    with open(xml, "r") as fh:
        lines = fh.readlines()
        for line in lines:
            if newtag in line:
                replace = False
                break

    tagcount = [0]

    def _write_xml(fh):
        for line in lines:
            if replace and oldtag in line:
                tagcount[0] += 1
                if tagcount[0] == 2:
                    fh.write(re.sub(f"{oldtag}", f"{newtag}\n\t{oldtag}", line))
                else:
                    fh.write(line)
            else:
                fh.write(line)

    writefile_with_mode(newxml, _write_xml)
    try:
        et.parse(newxml)
    except et.ParseError:
        logging.error(f"InvalidXMLError with appmenu [{newxml}]")
        logging.error("Exiting ...")
        sys.exit()


def copy_runtime_tree(source: Path, destination: Path) -> None:
    """Merge runtime files, dereferencing links and retaining directory modes."""
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.name.startswith(".nfs"):
            continue
        target = destination / entry.name
        if entry.is_dir():
            copy_runtime_tree(entry, target)
        else:
            shutil.copy2(entry, target)


def build_menu(installdir, deskenv, sh_prefix):
    climode = deskenv == "cli"

    copyfile_with_mode(
        Path("neurodesk/neurodesk-applications.menu"),
        installdir / "neurodesk-applications.menu",
    )
    copyfile_with_mode(
        Path("neurodesk/fetch_and_run.sh"), installdir / "fetch_and_run.sh", mode=0o755
    )
    copyfile_with_mode(
        Path("neurodesk/fetch_containers.sh"),
        installdir / "fetch_containers.sh",
        mode=0o755,
    )
    copyfile_with_mode(
        Path("neurodesk/configparser.sh"), installdir / "configparser.sh", mode=0o755
    )
    copyfile_with_mode(Path("config.ini"), installdir / "config.ini")
    copyfile_with_mode(Path("neurodesk/apps.json"), installdir / "apps.json")
    copyfile_with_mode(Path("neurodesk/bundles.json"), installdir / "bundles.json")
    copy_runtime_tree(
        Path("neurodesk/transparent-singularity"),
        installdir / "transparent-singularity",
    )

    if not climode:
        directories_path = installdir / "desktop-directories"
        icon_dir = installdir / "icons"
        write_directory_file("Neurodesk", directories_path, icon_dir)
        write_directory_file("All Applications", directories_path, icon_dir)
        write_directory_file("Functional Imaging", directories_path, icon_dir)
        write_directory_file("Workflows", directories_path, icon_dir)
        write_directory_file("Cryo EM", directories_path, icon_dir)
        write_directory_file("Data Organisation", directories_path, icon_dir)
        write_directory_file("Diffusion Imaging", directories_path, icon_dir)
        write_directory_file("Structural Imaging", directories_path, icon_dir)
        write_directory_file("Quantitative Imaging", directories_path, icon_dir)
        write_directory_file("Image Segmentation", directories_path, icon_dir)
        write_directory_file("Image Registration", directories_path, icon_dir)
        write_directory_file("Spectroscopy", directories_path, icon_dir)
        write_directory_file("Rodent Imaging", directories_path, icon_dir)
        write_directory_file("Image Reconstruction", directories_path, icon_dir)
        write_directory_file("Visualization", directories_path, icon_dir)
        write_directory_file("Programming", directories_path, icon_dir)
        write_directory_file("Quality Control", directories_path, icon_dir)
        write_directory_file("Shape Analysis", directories_path, icon_dir)
        write_directory_file("Spine", directories_path, icon_dir)
        write_directory_file("Electrophysiology", directories_path, icon_dir)
        write_directory_file("BIDS Apps", directories_path, icon_dir)
        write_directory_file("Machine Learning", directories_path, icon_dir)
        write_directory_file("Body", directories_path, icon_dir)
        write_directory_file("Hippocampus", directories_path, icon_dir)
        write_directory_file("Phase Processing", directories_path, icon_dir)
        write_directory_file("Molecular Biology", directories_path, icon_dir)
        write_directory_file("Statistics", directories_path, icon_dir)
        write_directory_file("Fetal Imaging", directories_path, icon_dir)
        write_directory_file("Arterial Spin Labelling", directories_path, icon_dir)

    appsjson = Path("neurodesk/apps.json").resolve(strict=True)
    (installdir / "icons").mkdir(exist_ok=True)
    bundles = load_bundles(
        Path("neurodesk/bundles.json"), json.loads(appsjson.read_text())
    )
    publish_bundles(bundles, installdir / "containers/modules")
    apps_from_json(climode, deskenv, installdir, appsjson, sh_prefix)

    # Neurodesk help app
    help_app = NeurodeskApp(
        deskenv=deskenv,
        installdir=installdir,
        name="Help",
        category="Neurodesk",
        launcher_command="firefox https://neurodesk.github.io/docs/neurodesktop",
    )
    write_app_sh(help_app)
    if not climode:
        write_app_menu(help_app)

    # Update Neurocommand app
    update_app = NeurodeskApp(
        deskenv=deskenv,
        installdir=installdir,
        name="Update",
        category="Neurodesk",
        launcher_command=f'cd {shlex.quote(str(installdir / "neurocommand"))}; bash build.sh --update --runsudo; read -p "Press enter to close this window ..."',
    )
    write_app_sh(update_app)
    if not climode:
        write_app_menu(update_app)

    # Remove any symlinks from local appdir
    # Prevents symlink recursion
    neurodesk_appdir = installdir / "applications"
    for file in neurodesk_appdir.glob("*"):
        if file.is_symlink():
            os.unlink(file)
