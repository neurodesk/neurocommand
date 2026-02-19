ssh rocky@203.101.224.225


#  run a check - takes about 4 hours to complete
sudo cvmfs_server check

# check if catalog is OK:
sudo cvmfs_server list-catalogs -e

# cleanup tags:
sudo cvmfs_server tag -l
sudo cvmfs_server tag -r generic-2026-02-13T21:28:30Z neurodesk.ardc.edu.au



# garbage collection:
sudo cvmfs_server gc neurodesk.ardc.edu.au

# setup auto tag cleanup:
#  change the configuration on the stratum 0 cvmfs in 
sudo vi /etc/cvmfs/repositories.d/neurodesk.ardc.edu.au/server.conf 
# to have
    # CVMFS_AUTO_TAG_TIMESPAN="2 weeks ago"