#!/usr/bin/env bash
set -e

echo "checking if CVMFS part works:"

sudo apt-get install lsb-release
wget --no-check-certificate https://cvmrepo.web.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb

echo "[DEBUG]: adding cfms repo"
sudo dpkg -i cvmfs-release-latest_all.deb
echo "[DEBUG]: apt-get update"
sudo apt-get update --allow-unauthenticated
echo "[DEBUG]: apt-get install cvmfs"
sudo apt-get install cvmfs --allow-unauthenticated

sudo mkdir -p /etc/cvmfs/keys/ardc.edu.au/


echo "-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwUPEmxDp217SAtZxaBep
Bi2TQcLoh5AJ//HSIz68ypjOGFjwExGlHb95Frhu1SpcH5OASbV+jJ60oEBLi3sD
qA6rGYt9kVi90lWvEjQnhBkPb0uWcp1gNqQAUocybCzHvoiG3fUzAe259CrK09qR
pX8sZhgK3eHlfx4ycyMiIQeg66AHlgVCJ2fKa6fl1vnh6adJEPULmn6vZnevvUke
I6U1VcYTKm5dPMrOlY/fGimKlyWvivzVv1laa5TAR2Dt4CfdQncOz+rkXmWjLjkD
87WMiTgtKybsmMLb2yCGSgLSArlSWhbMA0MaZSzAwE9PJKCCMvTANo5644zc8jBe
NQIDAQAB
-----END PUBLIC KEY-----" | sudo tee /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub


echo "CVMFS_USE_GEOAPI=no" | sudo tee /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf

echo 'CVMFS_SERVER_URL="http://cvmfs.neurodesk.org/cvmfs/@fqrn@"' | sudo tee -a /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf 

echo 'CVMFS_KEYS_DIR="/etc/cvmfs/keys/ardc.edu.au/"' | sudo tee -a /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf

echo "CVMFS_HTTP_PROXY=DIRECT" | sudo tee  /etc/cvmfs/default.local
echo "CVMFS_QUOTA_LIMIT=5000" | sudo tee -a  /etc/cvmfs/default.local

sudo cvmfs_config setup
sudo cvmfs_config chksetup

ls /cvmfs/neurodesk.ardc.edu.au/containers

cvmfs_config stat -v neurodesk.ardc.edu.au

bash run_transparent_singularity.sh --container itksnap_3.8.0_20201208.simg

# check if container exists on cvmfs

if [[ -d "/cvmfs/neurodesk.ardc.edu.au/containers/itksnap_3.8.0_20201208" ]]; then
   echo "Container exists in cvmfs"
else
   echo "Container does NOT exist!"
   exit 1
fi

# check if container link exists
if [ -L /home/runner/work/transparent-singularity/transparent-singularity/itksnap_3.8.0_20201208.simg ]; then
    echo "[DEBUG]: Container file link exists"
else 
    echo "[DEBUG]: Container file does not exist! Something went wrong."
    exit 1
fi

# check if transparent singularity generated executable output file:
FILE="/home/runner/work/transparent-singularity/transparent-singularity/itksnap"
if [ -f $FILE ];then
    echo "[DEBUG]: $FILE exists."
else
    echo "[DEBUG]: $FILE doesn't exist. Something went wrong with transparent singularity. "
    exit 1
fi
