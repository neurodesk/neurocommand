#!/usr/bin/env bash
set -e

#creating logfile with available containers
python3 neurodesk/write_log.py
pip3 install requests



# remove empty lines
sed -i '/^$/d' log.txt

# remove square brackets
sed -i 's/[][]//g' log.txt

# remove spaces around
sed -i -e 's/^[ \t]*//' -e 's/[ \t]*$//' log.txt

echo "[debug] logfile:"
cat log.txt
echo "[debug] logfile is at: $PWD"


mapfile -t arr < log.txt
for LINE in "${arr[@]}";
do
    echo "LINE: $LINE"
    export IMAGENAME_BUILDDATE="$(cut -d' ' -f1 <<< ${LINE})"
    echo "IMAGENAME_BUILDDATE: $IMAGENAME_BUILDDATE"

    IMAGENAME="$(cut -d'_' -f1,2 <<< ${IMAGENAME_BUILDDATE})"
    BUILDDATE="$(cut -d'_' -f3 <<< ${IMAGENAME_BUILDDATE})"
    echo "[DEBUG] IMAGENAME: $IMAGENAME"
    echo "[DEBUG] BUILDDATE: $BUILDDATE"

    if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/${IMAGENAME_BUILDDATE}.simg"; then
        echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg exists in nectar cloud"
        echo "[DEBUG] refresh timestamp to show it's still in use"
        rclone touch nectar:/neurodesk/${IMAGENAME_BUILDDATE}.simg
    fi 
done < log.txt

echo "[DEBUG] Deleting builds unused longer than 30days from object storage ..."
rclone delete --min-age 30d nectar:/neurodesk/

echo "[Debug] cleanup & syncing nectar containers to aws-neurocontainers"
rclone sync nectar:/neurodesk/ aws-neurocontainers-new:/neurocontainers/ --checksum --progress