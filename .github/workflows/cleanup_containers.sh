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
    IMAGENAME_BUILDDATE="$(cut -d' ' -f1 <<< "${LINE}")"
    echo "IMAGENAME_BUILDDATE: $IMAGENAME_BUILDDATE"

    IMAGENAME="$(cut -d'_' -f1,2 <<< ${IMAGENAME_BUILDDATE})"
    BUILDDATE="$(cut -d'_' -f3 <<< ${IMAGENAME_BUILDDATE})"
    echo "[DEBUG] IMAGENAME: $IMAGENAME"
    echo "[DEBUG] BUILDDATE: $BUILDDATE"
    OBJECT_KEY="${IMAGENAME_BUILDDATE}.simg"

    if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/${OBJECT_KEY}"; then
        echo "[DEBUG] ${OBJECT_KEY} exists in nectar cloud"
        echo "[DEBUG] refresh timestamp to show it's still in use"
        rclone touch "nectar:/neurodesk/${OBJECT_KEY}"

        # Refresh S3 object's LastModified timestamp (used by lifecycle expiration)
        if aws s3api head-object --bucket neurocontainers --key "${OBJECT_KEY}" > /dev/null 2>&1; then
            aws s3api copy-object \
              --bucket neurocontainers \
              --copy-source "neurocontainers/${OBJECT_KEY}" \
              --key "${OBJECT_KEY}" \
              --metadata-directive COPY > /dev/null
            echo "[DEBUG] Refreshed AWS timestamp for ${OBJECT_KEY}"
        else
            echo "[DEBUG] ${OBJECT_KEY} not found in AWS S3; skipping timestamp refresh"
        fi
    fi 
done < log.txt


echo "[DEBUG] Deleting builds unused longer than 30days from object storage ..."
rclone delete --min-age 30d nectar:/neurodesk/