ssh rocky@203.101.224.225
#  run a check - takes about 4 hours to complete
sudo cvmfs_server check

# check if catalog is OK:
sudo cvmfs_server list-catalogs -e
sudo cvmfs_server tag -l

# cleanup tags:
sudo cvmfs_server tag -a "before_cleanup"
sudo cvmfs_server tag -l neurodesk.ardc.edu.au | grep "update neurocommond for menus" | awk '{print $1}' | xargs -I {} sudo cvmfs_server tag -r {} neurodesk.ardc.edu.au

# garbage collection:
sudo cvmfs_server gc neurodesk.ardc.edu.au

# Display tags