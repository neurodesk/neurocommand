import json
import shutil

from test.support.artifacts import installed, refresh
from test.support.bundles import bundle_tree, catalog, manifest
from test.support.paths import ROOT
from test.support.shell import fake_runtime, write_executable


def launcher_tree(tmp_path, engine):
    install = tmp_path / "installation with spaces"
    install.mkdir()
    for name in ["fetch_and_run.sh", "configparser.sh"]:
        shutil.copy2(ROOT / "neurodesk" / name, install / name)
    shutil.copytree(
        ROOT / "neurodesk/transparent-singularity", install / "transparent-singularity"
    )
    (install / "config.ini").write_text("singularity_opts=--bind /configured-bind\n")
    (install / "apps.json").write_text(json.dumps(catalog()))
    (install / "bundles.json").write_text(json.dumps(manifest()))
    write_executable(
        install / "fetch_containers.sh",
        "#!/bin/bash\necho unexpected-fetch >&2\nexit 91\n",
    )
    root = bundle_tree(install, engine)
    directory, image = installed(install, "third", "1.0")
    (directory / "commands.txt").write_text("third\n")
    (directory / "env.txt").write_text("")
    assert refresh(directory, image).returncode == 0
    if engine != "lmod-lua":
        (root / "third/1.0.lua").unlink()
    runtime = fake_runtime(tmp_path, "fake runtime")
    return install, root, runtime
