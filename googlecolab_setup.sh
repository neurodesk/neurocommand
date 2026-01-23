#!/bin/bash

# install CVMFS packages for ubuntu:
sudo apt-get install lsb-release
wget https://cvmrepo.web.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb

echo "[DEBUG]: adding cfms repo"
sudo dpkg -i cvmfs-release-latest_all.deb >> /dev/null
echo "[DEBUG]: apt-get update"
sudo apt-get update --allow-unauthenticated >> /dev/null
echo "[DEBUG]: apt-get install cvmfs"
sudo apt-get install cvmfs tree --allow-unauthenticated >> /dev/null

# install apptainer for ubuntu:
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt update
sudo apt install -y apptainer-suid

sudo apptainer config fakeroot --add root

echo 'unshare -r apptainer "$@"' > /usr/bin/singularity_test
chmod +x /usr/bin/singularity_test
mv /usr/bin/singularity /usr/bin/singularity_backup
mv /usr/bin/singularity_test /usr/bin/singularity

#install datalad and lmod
sudo apt install -y datalad lmod

#install pip packages
pip install jupyterlmod==4.0.3 pandas nilearn matplotlib nipype osfclient ipyniivue==2.0.0

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
sudo cvmfs_config umount
sudo service autofs stop
sudo mkdir /cvmfs/neurodesk.ardc.edu.au
sudo mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au

cvmfs_config chksetup
ls /cvmfs/neurodesk.ardc.edu.au/
cvmfs_config stat -v neurodesk.ardc.edu.au
cvmfs_talk -i neurodesk.ardc.edu.au host info
