import sys
import argparse
import configparser
from pathlib import Path
import os
import signal
import logging

from neurodesk.build_menu import build_menu
from neurodesk.build_menu import neurodesk_xml

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# CLI signal handler for safe Ctrl-C
def signal_handler(signal, frame):
    logging.info("\nExiting ...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)

# Global settings
CONFIG_FILE = "config.ini"


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installdir", action="store")
    parser.add_argument("--deskenv", action="store")
    parser.add_argument("--appmenu", action="store")
    parser.add_argument("--appdir", action="store")
    parser.add_argument("--deskdir", action="store")
    parser.add_argument("--edit", action="store")

    args = parser.parse_args()
    return args


def main():
    if os.name != "posix":
        raise OSError

    args = get_args()
    config = configparser.ConfigParser()

    config["neurodesk"] = {
        "deskenv": "",
        "installdir": "",
        "appmenu": "",
        "appdir": "",
        "deskdir": "",
        "edit": "",
        "sh_prefix": "",
        "singularity_opts": "",
    }
    config.read(CONFIG_FILE)

    sections = iter(config.sections())
    next(sections)
    for section in sections:
        items = config.items(section)
        for item in items:
            config.set("neurodesk", item[0], item[1])
        config.remove_section(section)

    if args.installdir:
        config["neurodesk"]["installdir"] = str(args.installdir)
    if args.deskenv:
        config["neurodesk"]["deskenv"] = str(args.deskenv)
    if args.appmenu:
        config["neurodesk"]["appmenu"] = str(args.appmenu)
    if args.appdir:
        config["neurodesk"]["appdir"] = str(args.appdir)
    if args.deskdir:
        config["neurodesk"]["deskdir"] = str(args.deskdir)
    if args.edit:
        config["neurodesk"]["edit"] = str(args.edit)

    with open(CONFIG_FILE, "w+") as fh:
        config.write(fh)

    installdir = Path(config["neurodesk"]["installdir"]).resolve(strict=True)

    if not config["neurodesk"]["deskenv"] == "cli" and config["neurodesk"]["appmenu"]:
        appmenu = Path(config["neurodesk"]["appmenu"])
        appmenu_template = installdir / "local-applications.menu.template"
        new_appmenu = installdir / appmenu.name
        neurodesk_xml(appmenu_template, new_appmenu)

    build_menu(
        installdir, config["neurodesk"]["deskenv"], config["neurodesk"]["sh_prefix"]
    )


if __name__ == "__main__":
    main()
