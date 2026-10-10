# Contributor checks

Use Python 3.12, the version used by CI. Create a virtual environment and install the test and quality dependencies:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r test/requirements.txt -r maintenance/quality-requirements.txt
```

Install ShellCheck 0.11.0 and actionlint 1.7.7 on your PATH. Their versions are recorded in `maintenance/quality-tool-versions.env`; the install commands in `.github/workflows/quality.yml` download the Linux x86_64 releases. Other platforms can download the matching release from [ShellCheck](https://github.com/koalaman/shellcheck/releases) and [actionlint](https://github.com/rhysd/actionlint/releases).

Run the same entrypoint as CI and the apps.json queue gate:

```bash
python maintenance/check.py           # Source checks and the complete test suite
python maintenance/check.py quality   # Ruff, formatting, ShellCheck, actionlint, codespell
python maintenance/check.py tests     # Complete test suite, including skip reasons
python -m ruff format .               # Apply the required Python formatting
```

The correctness rules check Python syntax, invalid control flow and comparisons, and undefined names. ShellCheck checks every tracked `.sh` source. Generated wrappers and initialization templates are covered by the generated-artifact and shell-launcher tests. Suppressions next to shell statements explain deployment-provided configuration, external module initialization, and intentional legacy argument splitting. Prefer arrays for new shell argument lists.

Module integration tests skip an engine that is absent locally. To run them, install Lmod or Environment Modules. CI runs the entire suite once for each installed engine and fails before collection if the promised engine cannot initialize. Lmod's command metadata tests also need its environment initialized:

```bash
source /usr/share/lmod/lmod/init/bash
NEUROCOMMAND_TEST_ENGINE=lmod python maintenance/check.py tests
# For Environment Modules, installation is enough; use MODULES_CMD for a custom path.
NEUROCOMMAND_TEST_ENGINE=modules python maintenance/check.py tests
```

Skips for the other absent engine or unsupported old engine features are expected. Review the printed reasons when changing module generation or launcher behavior.

The `quality required` check requires source checks, the unit suite, both module-engine suites, the slim image build, and both CVMFS integration cases. Configure that check as required in GitHub branch protection. CI cannot run the image fetch integration locally without Docker, privileges, and access to the container registry/CVMFS service; its commands remain in `.github/workflows/test-neurocommand.yml`.

Update Python pins through Dependabot. To update ShellCheck or actionlint, change `maintenance/quality-tool-versions.env`, update the version in this document, and run the full entrypoint before merging.
