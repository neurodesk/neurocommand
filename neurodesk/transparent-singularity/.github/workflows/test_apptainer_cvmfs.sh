#!/usr/bin/env bash
set -e

sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt update
sudo apt install -y apptainer-suid

bash /home/runner/work/transparent-singularity/transparent-singularity/.github/workflows/test_transparent_singularity_cvmfs.sh