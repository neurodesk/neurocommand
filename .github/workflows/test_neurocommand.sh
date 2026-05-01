#!/usr/bin/env bash
set -e

APPTAINER_VERSION=1.4.5
apptainer_container="$(docker create ghcr.io/apptainer/apptainer:${APPTAINER_VERSION})"
apptainer_tmp="$(mktemp -d)"
trap 'docker rm -f "$apptainer_container" >/dev/null 2>&1 || true; rm -rf "$apptainer_tmp"' EXIT
docker cp "${apptainer_container}:/opt/apptainer" "${apptainer_tmp}/apptainer"
sudo rm -rf /opt/apptainer
sudo mv "${apptainer_tmp}/apptainer" /opt/apptainer
sudo ln -sf /opt/apptainer/bin/apptainer /usr/local/bin/apptainer
sudo ln -sf /opt/apptainer/bin/singularity /usr/local/bin/singularity

echo "checking if neurodesk installs and a containers gets downloaded correctly"

echo "python version is ... "
python --version
echo "apptainer version is ... "
apptainer --version
echo "where am I"
pwd
bash build.sh --cli --lxde
bash containers.sh all
bash /home/runner/work/neurocommand/neurocommand/local/fetch_containers.sh niimath 1.0.0 20240902 niimath 


# check if container file exists
if [ -f /home/runner/work/neurocommand/neurocommand/local/containers/niimath_1.0.0_20240902/niimath_1.0.0_20240902.simg ]; then
    echo "[DEBUG]: test_neurocommand.sh Container file exists"
else 
    echo "[DEBUG]: test_neurocommand.sh Container file does not exist! Something went wrong when downloading."
    exit 1
fi

# check if transparent singularity generated executable output file:
FILE="/home/runner/work/neurocommand/neurocommand/local/containers/niimath_1.0.0_20240902/niimath"
if [ -f $FILE ];then
    echo "[DEBUG]: test_neurocommand.sh $FILE exists."
else
    echo "[DEBUG]: test_neurocommand.sh $FILE doesn't exist. Something went wrong with transparent singularity. Trying again."
    rm -rf /home/runner/work/neurocommand/neurocommand/local/containers/niimath_1.0.0_20240902/niimath_1.0.0_20240902.simg
    bash /home/runner/work/neurocommand/neurocommand/local/fetch_containers.sh niimath 1.0.0 20240902 niimath
    if [ -f $FILE ];then
        echo "[DEBUG]: test_neurocommand.sh $FILE exists."
    else 
        echo "[DEBUG]: test_neurocommand.sh $FILE doesn't exist. Something went wrong with transparent singularity. Trying again."
    fi
fi
