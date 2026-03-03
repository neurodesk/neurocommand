"""Generate the menu items."""
import configparser
import json
import os
import sys
from pathlib import Path
import re
from typing import Callable, List, Optional, Text, TextIO
import xml.etree.ElementTree as et
from xml.dom import minidom
import shutil
import logging
import distutils.dir_util


def chmod_if_new(path: Path, mode: int, existed_before: bool) -> None:
    """Set file mode only when this run created the target file."""
    if not existed_before:
        os.chmod(path, mode)


def _stat_mode(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    return path.stat().st_mode & 0o777


def _restore_mode(path: Path, existed_before: bool, was_recreated: bool, previous_mode: Optional[int]) -> None:
    """Preserve mode when replacing an existing non-writable file."""
    if existed_before and was_recreated and previous_mode is not None:
        os.chmod(path, previous_mode)


def writefile_with_mode(path: Path, writer: Callable[[TextIO], None], mode: Optional[int] = None) -> None:
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
    file_path = file_dir/f"{name.lower().replace(' ', '-')}.directory"
    icon_path = icon_dir/f"{name.lower().split()[0]}.png"
    if name == 'Neurodesk':
        icon_path = icon_dir/f"aedapt.png"
    icon_src = (Path(__file__).parent/'icons'/icon_path.name)
    try:
        copyfile_with_mode(icon_src, icon_path)
    except FileNotFoundError:
        logging.warning(f'{icon_src} not found')
        icon_src = (Path(__file__).parent/'icons/neurodesk.png')
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
    file_dir = installdir/"desktop-directories/apps"
    icon_dir = installdir/f"icons"
    file_path = write_directory_file(name, file_dir, icon_dir)

    # Add entry to `.menu` file
    menu_path = installdir/"neurodesk-applications.menu"
    with open(menu_path, "r") as xml_file:
        s = xml_file.read()
    s = re.sub(r"\s+(?=<)", "", s)
    root = et.fromstring(s)
    category_name = f'{category.lower().replace(" ", "-")}'
    for menu_el in root.findall(".//Menu/Menu"):
        if menu_el[2][0][0].text == category_name:
        # menu_el = root.findall("./Menu/Menu")[0]
            sub_el = et.SubElement(menu_el, "Menu")
            name_el = et.SubElement(sub_el, "Name")
            name_el.text = name.capitalize()
            dir_el = et.SubElement(sub_el, "Directory")
            dir_el.text = f'neurodesk/apps/{file_path.name}'
            include_el = et.SubElement(sub_el, "Include")
            and_el = et.SubElement(include_el, "And")
            cat_el = et.SubElement(and_el, "Category")
            cat_el.text = name.replace(" ", "-")
            cat_el.text = f"{cat_el.text}"
            xmlstr = minidom.parseString(et.tostring(root)).toprettyxml(indent="\t")
            def _write_menu(f):
                f.write('<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"\n ')
                f.write('"http://www.freedesktop.org/standards/menu-spec/1.0/menu.dtd">\n\n')
                f.write(xmlstr[xmlstr.find("?>") + 3 :])
            writefile_with_mode(menu_path, _write_menu, mode=0o644)
            break


class NeurodeskApp:
    def __init__(
        self,
        deskenv: Text,
        installdir: Path,
        name: Text,
        sh_prefix: Text = "",
        version: Text = "",
        category: Text = "",
        exec: Text = "",
        terminal: bool = True,
        apptainer_args: Optional[List[str]] = None,
        ):
        """Add an application to the menu.

        Parameters
        ----------
        name : Text
            The name of the application.
        version : Text
            The version of the application.
        exec : Text
            The command to run when clicking on the application item.
        category : Text
            The category defining the menu in which the application must be added.
        terminal : bool
            If set to ``True``, a terminal is opened when launching the application.
        """
        self.deskenv = deskenv
        self.installdir = installdir
        self.name = name
        self.sh_prefix = sh_prefix
        self.version= version
        self.category = category
        self.exec = exec #TODO change exec to safer variable name
        self.terminal = terminal
        self.apptainer_args = apptainer_args or []

    def app_names(self):
        self.basename = f"{self.name.lower().replace(' ', '-').replace('.', '_')}"
        self.category = f"{self.category}"
        if self.exec:
            # assumes that executable name is before the dash and after the dash the normal container name and version
            self.container_name = self.name.split("-")[1]
            self.exec_name = self.name.split("-")[0] + " " + self.name.split("-")[1].split(" ")[1]
        else: 
            self.container_name = self.name
            self.exec_name = self.name

    def add_app_sh(self, sh_exec=""):
        fetch_and_run_sh = self.installdir/"fetch_and_run.sh"
        self.bin_path = self.installdir/"bin"
        self.bin_path.mkdir(exist_ok=True)
        self.sh_path = self.bin_path/f"{self.basename}.sh"
        def _write_app_sh(self_sh_file):
            self_sh_file.write("#!/usr/bin/env bash\n")
            self_sh_file.write(f"{self.sh_prefix} ")
            if sh_exec:
                self_sh_file.write(f"{sh_exec}")
            elif self.deskenv == 'mate':
                self_sh_file.write(f"{str(fetch_and_run_sh)} {self.container_name} {self.version} {self.exec} $@")
            else:
                self_sh_file.write(f"{str(fetch_and_run_sh)} {self.container_name} {self.version} {self.exec} $@")
            self_sh_file.write('\n')
        writefile_with_mode(self.sh_path, _write_app_sh, mode=0o755)

    def add_app_menu(self) -> None:
        icon_path = self.installdir/f"icons/{self.name.split()[0]}.png"
        icon_src = Path(__file__).parent/'icons'/icon_path.name
        try:
            copyfile_with_mode(icon_src, icon_path)
        except FileNotFoundError:
            logging.warning(f'{icon_src} not found')
            icon_src = (Path(__file__).parent/'icons/neurodesk.png')
            copyfile_with_mode(icon_src, icon_path)
        entry = configparser.ConfigParser()
        entry.optionxform = str

        if self.deskenv == 'mate':
            entry["Desktop Entry"] = {
                "Name": self.exec_name,
                "GenericName": self.exec_name,
                "Comment": self.name + " " + self.version,
                "Exec": f"mate-terminal --window --title \"{self.name}\" -e \'/bin/bash {str(self.sh_path)}\'",
                "Icon": icon_path,
                "Type": "Application",
                "Categories": self.category
            }
        else:
            entry["Desktop Entry"] = {
                "Name": self.exec_name,
                "GenericName": self.exec_name,
                "Comment": self.name + " " + self.version,
                "Exec": f"/bin/bash {str(self.sh_path)}",
                "Icon": icon_path,
                "Type": "Application",
                "Categories": self.category,
                "Terminal": str(self.terminal).lower()
            }

        applications_path = self.installdir/"applications"
        applications_path.mkdir(exist_ok=True)
        desktop_path = applications_path/f"{self.basename}.desktop"

        def _write_desktop(desktop_file):
            entry.write(desktop_file, space_around_delimiters=False)
        writefile_with_mode(desktop_path, _write_desktop, mode=0o644)


def apps_from_json(cli, deskenv: Text, installdir: Path, appsjson: Path, sh_prefix='')  -> None:
    # Read applications file
    with open(appsjson, "r") as json_file:
        menu_entries = json.load(json_file)

    for menu_name, menu_data in menu_entries.items():
        # Add submenu
        if not cli:
            add_menu(installdir, menu_name, 'all applications')
            for category in menu_data.get("categories") or []:
                add_menu(installdir, menu_name, category)
        for app_name, app_data in menu_data.get("apps", {}).items():
            app = NeurodeskApp(
                deskenv=deskenv,
                installdir=installdir,
                sh_prefix=sh_prefix,
                name=app_name,
                category=menu_name.replace(" ", "-"),
                **app_data)
            app.app_names()
            app.add_app_sh()
            if not cli:
                app.add_app_menu()


def neurodesk_xml(xml: Path, newxml: Path) -> None:
    oldtag = '<Menu>'
    newtag = '<MergeFile>neurodesk-applications.menu</MergeFile>'
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
                    fh.write(re.sub(f'{oldtag}', f'{newtag}\n\t{oldtag}', line))
                else:
                    fh.write(line)
            else:
                fh.write(line)
    writefile_with_mode(newxml, _write_xml)
    try:
        et.parse(newxml)
    except et.ParseError:
        logging.error(f'InvalidXMLError with appmenu [{newxml}]')
        logging.error('Exiting ...')
        sys.exit()


def build_menu(installdir, deskenv, sh_prefix):
    climode = False
    if deskenv == 'cli':
        climode = True

    copyfile_with_mode(Path('neurodesk/neurodesk-applications.menu'), installdir/'neurodesk-applications.menu')
    copyfile_with_mode(Path('neurodesk/fetch_and_run.sh'), installdir/'fetch_and_run.sh', mode=0o755)
    copyfile_with_mode(Path('neurodesk/fetch_containers.sh'), installdir/'fetch_containers.sh', mode=0o755)
    copyfile_with_mode(Path('neurodesk/configparser.sh'), installdir/'configparser.sh', mode=0o755)
    copyfile_with_mode(Path('config.ini'), installdir/'config.ini')
    distutils.dir_util.copy_tree('neurodesk/transparent-singularity', str(installdir/'transparent-singularity'))

    if not climode:
        directories_path = installdir/"desktop-directories"
        icon_dir = installdir/"icons"
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

    appsjson = Path('neurodesk/apps.json').resolve(strict=True)
    (installdir/'icons').mkdir(exist_ok=True)
    apps_from_json(climode, deskenv, installdir, appsjson, sh_prefix)

    # Neurodesk help app
    help_app = NeurodeskApp(
        deskenv=deskenv,
        installdir=installdir,
        name="Help",
        category="Neurodesk")
    help_app.app_names()
    help_app.add_app_sh("firefox https://neurodesk.github.io/docs/neurodesktop")
    if not climode:
        help_app.add_app_menu()

    # Update Neurocommand app
    update_app = NeurodeskApp(
        deskenv=deskenv,
        installdir=installdir,
        name="Update",
        category="Neurodesk")
    update_app.app_names()
    update_app.add_app_sh(f"cd {installdir}/neurocommand; bash build.sh --update --runsudo; read -p \"Press enter to close this window ...\"")
    if not climode:
        update_app.add_app_menu()

    # Remove any symlinks from local appdir
    # Prevents symlink recursion
    neurodesk_appdir = installdir/'applications'
    for file in neurodesk_appdir.glob('*'):
        if file.is_symlink():
            os.unlink(file)
