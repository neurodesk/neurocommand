#!/bin/bash
# New rocky linux 10 machine
ssh rocky@203.101.224.225

sudo yum install vim htop gcc git screen rsync apptainer

# disable cron jobs on old machine and copy keys/cronjobs over:
sudo vi /etc/cron.d/cvmfs_resign
sudo vi /etc/cron.d/sync_containers_to_cvmfs

sudo scp -p /etc/cvmfs/keys/* rocky@203.101.224.225:~/keys
sudo scp -p /etc/cron.d/cvmfs_resign rocky@203.101.224.225:~/cron.d/
sudo scp -p /etc/cron.d/sync_containers_to_cvmfs rocky@203.101.224.225:~/cron.d/


sudo -i

ssh-keygen

# copy repo
rsync -aHvz --progress --rsync-path="sudo rsync" /srv/cvmfs/neurodesk.ardc.edu.au/ rocky@203.101.224.225:/srv/cvmfs/neurodesk.ardc.edu.au/


# setup on new machine
sudo yum install -y https://cvmrepo.s3.cern.ch/cvmrepo/yum/cvmfs-release-latest.noarch.rpm
sudo yum install -y cvmfs-server cvmfs

sudo mkdir -p /etc/cvmfs/
sudo cp -r ~/keys /etc/cvmfs/

# Set permissions 
# Private keys must be readable only by the owner
sudo chown -R root:root /etc/cvmfs/keys/*
sudo chmod 600 /etc/cvmfs/keys/*.key
sudo chmod 600 /etc/cvmfs/keys/*.masterkey
sudo chmod 644 /etc/cvmfs/keys/*.pub
sudo chmod 644 /etc/cvmfs/keys/*.crt

sudo groupadd -r cvmfs

# Create the user
# -r: system account
# -g cvmfs: primary group
# -d /var/lib/cvmfs: home directory
# -s /sbin/nologin: no shell access
# -c: comment
sudo useradd -r -g cvmfs -d /var/lib/cvmfs -s /sbin/nologin -c "CernVM-FS service account" cvmfs

sudo chown -R cvmfs:cvmfs /srv/cvmfs/neurodesk.ardc.edu.au/

# migrate homedirectory and scripts of old server
rsync -aHvz --progress --rsync-path="sudo rsync" ~ rocky@203.101.224.225:~

# Install Apache
sudo dnf install -y httpd

# Start it and enable it to run on boot
sudo systemctl enable --now httpd

sudo cvmfs_server import -o cvmfs neurodesk.ardc.edu.au

sudo cvmfs_server eliminate-hardlinks neurodesk.ardc.edu.au




sudo systemctl daemon-reload
sudo cvmfs_server update-info

ls /cvmfs/neurodesk.ardc.edu.au

sudo cvmfs_server resign neurodesk.ardc.edu.au

sudo cvmfs_server transaction neurodesk.ardc.edu.au

sudo cvmfs_server publish neurodesk.ardc.edu.au

sudo chmod a+rwx /etc/cron.d/
cp ~/cron.d/* /etc/cron.d/
ls /etc/cron.d/
# activate cronjobs on new server
# uncomment! 
vi /etc/cron.d/cvmfs_resign
vi /etc/cron.d/sync_containers_to_cvmfs


# test on one of our stratum 1 with the IP of the new cvmfs-server. Then move DNS over

mv ~/ec2-user/* .
rmdir ~/ec2-user