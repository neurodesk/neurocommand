import shutil
import subprocess

from test.support.paths import TRANSPARENT_SINGULARITY
from test.support.shell import clean_env


def installed(tmp_path, name="demo", version="1.0"):
    directory = tmp_path / "containers" / f"{name}_{version}_20260629"
    shutil.copytree(TRANSPARENT_SINGULARITY, directory)
    image = f"{directory.name}.simg"
    (directory / image).touch()
    (directory / "commands.txt").write_text("demo\ndemo\n.hidden\nlib.so\n")
    (directory / "env.txt").write_text(
        'DEPLOY_ENV_TEST_VALUE=BASEPATH/a=b "quoted" $d [e] \\ tail\n'
    )
    (directory / "README.md").write_text(
        'Help "quoted" $d [error boom] \\ { unmatched\n'
    )
    return directory, image


def refresh(directory, image, env=None):
    return subprocess.run(
        [
            "bash",
            str(directory / "run_transparent_singularity.sh"),
            "--container",
            image,
            "--refresh",
        ],
        env=env or clean_env(),
        capture_output=True,
        text=True,
    )
