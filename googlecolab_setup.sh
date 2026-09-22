#!/bin/bash
set -euo pipefail

# Use the notebook's Python interpreter when supplied by the calling cell.
PYTHON="${NEURODESK_COLAB_PYTHON:-python3}"
export LD_PRELOAD=""
export DEBIAN_FRONTEND=noninteractive
cd /content

# install CVMFS packages for ubuntu:
sudo apt-get install -y lsb-release
curl -fsSL https://cvmrepo.web.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb -o cvmfs-release-latest_all.deb

echo "[DEBUG]: adding cfms repo"
sudo dpkg -i cvmfs-release-latest_all.deb >> /dev/null
echo "[DEBUG]: apt-get update"
sudo apt-get update >> /dev/null
echo "[DEBUG]: apt-get install cvmfs"
sudo apt-get install -y cvmfs tree >> /dev/null

# install apptainer for ubuntu:
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt update
sudo apt install -y apptainer-suid

sudo apptainer config fakeroot --add root

# Keep the original launcher when setup is run more than once.
if [ ! -e /usr/bin/singularity_backup ]; then
    sudo mv /usr/bin/singularity /usr/bin/singularity_backup
fi
printf '#!/bin/sh\nexec unshare -r apptainer "$@"\n' | sudo tee /usr/bin/singularity >/dev/null
sudo chmod +x /usr/bin/singularity

# DataLad is unavailable in some Colab apt repositories. Do not let it
# prevent installation of Lmod, which loads all Neurodesk modules.
sudo apt-get install -y lmod git

# Preserve Colab's pinned dependencies and NumPy compatibility. pip will
# select a compatible nilearn instead of upgrading these shared packages.
"$PYTHON" - <<'PYCONSTRAINTS'
from importlib.metadata import requires
from pathlib import Path
from packaging.requirements import Requirement

constraints = ["numpy<2.3"]
for spec in requires("google-colab") or []:
    requirement = Requirement(spec)
    if requirement.name in {"pandas", "requests"}:
        constraints.append(str(requirement))
Path("/content/neurodesk-colab-constraints.txt").write_text("\n".join(constraints) + "\n")
PYCONSTRAINTS
"$PYTHON" -m pip install -c /content/neurodesk-colab-constraints.txt \
    jupyterlmod==4.0.3 pandas nilearn matplotlib nipype osfclient ipyniivue==2.0.0 \
    datalad datalad-installer
# Colab cannot answer the installer's interactive privilege prompt.
datalad-installer --sudo ok git-annex -m datalad/packages
datalad --version
git annex version

#setup cvmfs
mkdir -p /etc/cvmfs/keys/ardc.edu.au/
echo "-----BEGIN PUBLIC KEY-----" | sudo tee /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwUPEmxDp217SAtZxaBep" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "Bi2TQcLoh5AJ//HSIz68ypjOGFjwExGlHb95Frhu1SpcH5OASbV+jJ60oEBLi3sD" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "qA6rGYt9kVi90lWvEjQnhBkPb0uWcp1gNqQAUocybCzHvoiG3fUzAe259CrK09qR" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "pX8sZhgK3eHlfx4ycyMiIQeg66AHlgVCJ2fKa6fl1vnh6adJEPULmn6vZnevvUke" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "I6U1VcYTKm5dPMrOlY/fGimKlyWvivzVv1laa5TAR2Dt4CfdQncOz+rkXmWjLjkD" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "87WMiTgtKybsmMLb2yCGSgLSArlSWhbMA0MaZSzAwE9PJKCCMvTANo5644zc8jBe" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "NQIDAQAB" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "-----END PUBLIC KEY-----" | sudo tee -a /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
echo "CVMFS_USE_GEOAPI=yes" | sudo tee /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf
echo 'CVMFS_SERVER_URL="http://s1osggoc-cvmfs.openhtc.io:8080/cvmfs/@fqrn@;http://s1fnal-cvmfs.openhtc.io:8080/cvmfs/@fqrn@;http://s1sampa-cvmfs.openhtc.io:8080/cvmfs/@fqrn@;http://s1nikhef-cvmfs.openhtc.io/cvmfs/@fqrn@;http://s1bnl-cvmfs.openhtc.io/cvmfs/@fqrn@"' | sudo tee -a /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf
echo 'CVMFS_KEYS_DIR="/etc/cvmfs/keys/ardc.edu.au/"' | sudo tee -a /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf
echo "CVMFS_HTTP_PROXY=DIRECT" | sudo tee  /etc/cvmfs/default.local
echo "CVMFS_QUOTA_LIMIT=5000" | sudo tee -a  /etc/cvmfs/default.local
cvmfs_config setup

# Disabling autofs is needed, otherwise autofs is not fast enough to mount CVMFS and it will complain about it with "too many symbolic errors"
# Colab has no systemd; stopping an absent autofs service is harmless.
sudo service autofs stop || true
sudo mkdir -p /cvmfs/neurodesk.ardc.edu.au
if ! mountpoint -q /cvmfs/neurodesk.ardc.edu.au; then
    sudo mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au
fi

ls /cvmfs/neurodesk.ardc.edu.au/
cvmfs_config stat -v neurodesk.ardc.edu.au
cvmfs_talk -i neurodesk.ardc.edu.au host info

# A child shell cannot set the parent notebook kernel's environment. Write
# the values as JSON so the notebook only needs to import them after setup.
"$PYTHON" - <<'PYENV'
import json
from pathlib import Path

module_root = Path("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules")
module_paths = sorted(str(path) for path in module_root.iterdir() if path.is_dir())
if not module_paths:
    raise RuntimeError("CVMFS mounted but no Neurodesk module directories were found")
lmod_cmd = Path("/usr/share/lmod/lmod/libexec/lmod")
if not lmod_cmd.is_file():
    raise RuntimeError("Lmod installation failed")
environment = {
    "LD_PRELOAD": "",
    "APPTAINER_BINDPATH": "/content",
    "LMOD_CMD": str(lmod_cmd),
    "MODULEPATH": ":".join(module_paths),
}
Path("/content/neurodesk-colab-env.json").write_text(json.dumps(environment, indent=2) + "\n")
print("Neurodesk setup complete. Environment saved to /content/neurodesk-colab-env.json")
PYENV
