import os
import textwrap

def write_executable(path, text):
    path.write_text(textwrap.dedent(text).lstrip())
    path.chmod(0o755)


def clean_env(**overrides):
    env = {k: v for k, v in os.environ.items() if k != "BASH_ENV"}
    env.update(overrides)
    return env


def fake_runtime(directory, name):
    path = directory / name
    write_executable(path, '''#!/usr/bin/env python3
import json, os, sys
print(json.dumps({'runtime': os.path.basename(sys.argv[0]), 'argv': sys.argv[1:],
                  'nv': [os.getenv('APPTAINER_NV'), os.getenv('SINGULARITY_NV')]}))
''')
    return path
