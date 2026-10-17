"""Generate webapps against the catalog staged in the consolidated app update."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def generate_webapps(neurocontainers_path: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="webapps-") as directory:
        output = Path(directory) / "webapps.json"
        subprocess.run(
            [
                sys.executable,
                str(neurocontainers_path / "tools/generate_webapps_json.py"),
                "--output",
                str(output),
            ],
            check=True,
        )
        return json.loads(output.read_text())


def select_available_webapps(generated: dict, current: dict, catalog: dict) -> dict:
    def available(config):
        module = config["module"]
        return f"{module} {config['version']}" in catalog.get(module, {}).get(
            "apps", {}
        )

    selected = {}
    for name, config in generated["webapps"].items():
        if available(config):
            selected[name] = config
        elif name in current["webapps"] and available(current["webapps"][name]):
            selected[name] = current["webapps"][name]
    return copy.deepcopy({**generated, "webapps": selected})
