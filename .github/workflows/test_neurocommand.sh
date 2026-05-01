#!/usr/bin/env bash
set -e

sudo apt update
sudo apt install -y curl gnupg
. /etc/os-release
sudo mkdir -p /etc/apt/keyrings
curl --fail --location --show-error --silent \
    --retry 5 --retry-all-errors --connect-timeout 20 \
    "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xF6B0F5193D4F3301EF491FF0AFE36534FC6218AE" \
    | sudo gpg --dearmor --yes --output /etc/apt/keyrings/apptainer-ppa.gpg
printf 'Types: deb\nURIs: https://ppa.launchpadcontent.net/apptainer/ppa/ubuntu\nSuites: %s\nComponents: main\nSigned-By: /etc/apt/keyrings/apptainer-ppa.gpg\n' "$VERSION_CODENAME" \
    | sudo tee /etc/apt/sources.list.d/apptainer-ppa.sources > /dev/null
sudo apt -o Acquire::Retries=5 update
sudo apt install -y apptainer apptainer-suid

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
