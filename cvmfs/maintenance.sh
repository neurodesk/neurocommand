#  run a check - takes about 4 hours to complete
sudo cvmfs_server check

# check if catalog is OK:
sudo cvmfs_server list-catalogs -e
sudo cvmfs_server tag -l


# garbage collection:
sudo cvmfs_server gc neurodesk.ardc.edu.au

# Display tags