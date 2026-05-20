import argparse
import json
from pathlib import Path


def visibility_flag(data, name, default=True):
    return data.get(name, default) is not False


def app_log_id(app_name, app_data):
    return f"{app_name.replace(' ', '_')}_{app_data['version']}"


def hidden_applist_entries(apps_json_path):
    if apps_json_path is None or not apps_json_path.exists():
        return set()

    with apps_json_path.open() as json_file:
        menu_entries = json.load(json_file)

    hidden = set()
    for menu_data in menu_entries.values():
        default_show_in_applist = visibility_flag(menu_data, "show_in_applist")
        for app_name, app_data in menu_data.get("apps", {}).items():
            if not visibility_flag(app_data, "show_in_applist", default_show_in_applist):
                hidden.add(app_log_id(app_name, app_data))
    return hidden


def default_apps_json_path():
    candidates = [
        Path("neurodesk/apps.json"),
        Path("../neurodesk/apps.json"),
        Path(__file__).resolve().parents[1] / "neurodesk/apps.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def process_text_to_json(log_path=Path("log.txt"), output_path=Path("applist.json"), apps_json_path=None):
    my_dict = {}
    val = []
    hidden_entries = hidden_applist_entries(apps_json_path)

    with log_path.open() as f:
        for line in f:
            line = line.split()
            if not line or line[0] in hidden_entries:
                continue
            val.append({"application": line[0], "categories": ' '.join(line[1:]).replace("categories:","").rstrip(',').split(",")})
        my_dict['list'] = val
        
    with output_path.open('w') as fp:
        json.dump(my_dict, fp, sort_keys=True, indent=4)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Convert cvmfs/log.txt to applist.json.")
    parser.add_argument("--log-path", default=Path("log.txt"), type=Path)
    parser.add_argument("--output", default=Path("applist.json"), type=Path)
    parser.add_argument("--apps-json", default=default_apps_json_path(), type=Path)
    args = parser.parse_args()

    process_text_to_json(
        log_path=args.log_path,
        output_path=args.output,
        apps_json_path=args.apps_json,
    )
