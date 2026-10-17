import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_webapp_module_versions_exist_in_app_catalog():
    webapps = json.loads((ROOT / "neurodesk/webapps.json").read_text())["webapps"]
    catalog = json.loads((ROOT / "neurodesk/apps.json").read_text())

    missing = [
        f"{name}: {config['module']}/{config['version']}"
        for name, config in webapps.items()
        if f"{config['module']} {config['version']}"
        not in catalog.get(config["module"], {}).get("apps", {})
    ]

    assert missing == []
