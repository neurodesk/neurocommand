ssh rocky@203.101.224.225
#  run a check - takes about 4 hours to complete
sudo cvmfs_server check

# check if catalog is OK:
sudo cvmfs_server list-catalogs -e
sudo cvmfs_server tag -l

# cleanup tags:
sudo cvmfs_server tag -a "before_cleanup"
sudo cvmfs_server tag -l
sudo cvmfs_server tag -r generic-2026-02-11T19:58:40Z neurodesk.ardc.edu.au
sudo cvmfs_server tag -l
sudo cvmfs_server tag -r generic-2026-02-13T21:28:30Z neurodesk.ardc.edu.au


screen -R
sudo cvmfs_server tag -l neurodesk.ardc.edu.au | \
grep "update neurocommond for menus" | \
awk '{print $1}' | \
while read -r tag; do
    echo "Processing tag: $tag"
    # Pipe 'y' into the command to auto-confirm
    echo y | sudo cvmfs_server tag -r "$tag" neurodesk.ardc.edu.au
done

# garbage collection:
sudo cvmfs_server gc neurodesk.ardc.edu.au

# Display tags
#  change the configuration on the stratum 0 cvmfs in 
sudo vi /etc/cvmfs/repositories.d/neurodesk.ardc.edu.au/server.conf 
# to have
    CVMFS_AUTO_TAG_TIMESPAN="2 weeks ago"