"""Generate the menu items."""
import configparser
import json
from pathlib import Path
from typing import Text, List, Optional

def add_app(
    name: Text,
    version: Text,
    category: Text,
    exec: Text = "",
    apptainer_args: Optional[List[str]] = None,
    terminal: bool = True,
) -> None:
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
    log = configparser.ConfigParser()
    log.optionxform = str
    log[" " + name.replace(" ", "_") + "_" + version + " categories:" + category] = { }
    with open('log.txt', "a",) as log_file:
        log.write(log_file, space_around_delimiters=False)


if __name__ == "__main__":
    # Read applications file
    # with open(Path("/home/ec2-user/neurodesk/neurodesk/apps.json"), "r") as json_file:
    with open(Path("./neurodesk/apps.json"), "r") as json_file:
        menu_entries = json.load(json_file)

    for menu_name, menu_data in menu_entries.items():
        for app_name, app_data in menu_data.get("apps", {}).items():
            category_list = ''
            for category in menu_data.get("categories") or []:
                category_list = category_list + category + ','
            # Add application, but only if it's not a sub-program of a main container - indicated by dash in program name
            if not "-" in (app_name):
                add_app(app_name, category=category_list, **app_data)
