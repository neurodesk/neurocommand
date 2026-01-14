#!/usr/bin/env bash
set -e

echo "checking if containers are built"

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

export IMAGE_HOME="/storage/tmp"

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
    else
        echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg does not exist in released files in nectar cloud"
        echo "[DEBUG] check if it exists in AWS: "
        if curl --output /dev/null --silent --head --fail "https://neurocontainers.s3.us-east-2.amazonaws.com/${IMAGENAME_BUILDDATE}.simg"; then
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg exists AWS"

            # check if the image is already in the local builder cache:
            if [ -f $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg ]; then
                echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg already exists in cache at $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg"
            else
                echo "[WARNING] ${IMAGENAME_BUILDDATE}.simg does not exist in cache at $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg"
                echo "[WARNING] Downloading now ... this shouldn't be necessary so something is wrong"
                curl --output "$IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg" "https://neurocontainers.s3.us-east-2.amazonaws.com/${IMAGENAME_BUILDDATE}.simg"
            fi

        else
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg does not exist in any cloud object storage"

            # check if the image is already in the local builder cache:
            echo "[DEBUG] Checking if ${IMAGENAME_BUILDDATE}.simg exists in local cache at $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg"
            if [ -f $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg ]; then
                echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg already exists in cache at $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg"
            else
                echo "[WARNING] ${IMAGENAME_BUILDDATE}.simg does not exist in local cache at $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg"
                echo "[DEBUG] Rebuilding from docker"
                # image was not released previously and is not in cache - rebuild from docker:
                # check if there is enough free disk space on the runner:
                FREE=`df -k --output=avail "$PWD" | tail -n1`   # df -k not df -h
                echo "[DEBUG] This runner has ${FREE} free disk space"
                if [[ $FREE -lt 20485760 ]]; then               # 20G = 10*1024*1024k
                    echo "[DEBUG] This runner has not enough free disk space .. cleaning up!"
                    bash .github/workflows/free-up-space.sh
                    FREE=`df -k --output=avail "$PWD" | tail -n1`   # df -k not df -h
                    echo "[DEBUG] This runner has ${FREE} free disk space after cleanup"
                fi

                if [ -n "$singularity_setup_done" ]; then
                    echo "Setup already done. Skipping."
                else
                    #install apptainer
                    sudo apt update > /dev/null 2>&1
                    sudo apt install -y software-properties-common > /dev/null 2>&1
                    sudo add-apt-repository -y ppa:apptainer/ppa > /dev/null 2>&1
                    sudo apt update > /dev/null 2>&1
                    sudo apt install -y apptainer apptainer-suid > /dev/null 2>&1

                    export singularity_setup_done="true"
                fi

                echo "[DEBUG] singularity building docker://vnmd/$IMAGENAME:$BUILDDATE"
                singularity build "$IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg"  docker://vnmd/$IMAGENAME:$BUILDDATE
            fi
        fi

        if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/${IMAGENAME_BUILDDATE}.simg" && curl --output /dev/null --silent --head --fail "https://neurocontainers.s3.us-east-2.amazonaws.com/${IMAGENAME_BUILDDATE}.simg"; then
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg was freshly released :)"
            echo "[DEBUG] PROCEEDING TO NEXT LINE"
            echo "[DEBUG] Cleaning up ..."
            rm -rf /home/runner/.singularity/docker
            rm -rf $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg
        else
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg does not exist yet in AWS and Nectar. Something is WRONG"
            echo "[DEBUG] Trying again using rclone copy instead of move"
            rclone copy $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg nectar:/neurodesk/
            rclone copy $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg aws-neurocontainers-new:/neurocontainers/
            
            if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/${IMAGENAME_BUILDDATE}.simg" && curl --output /dev/null --silent --head --fail "https://neurocontainers.s3.us-east-2.amazonaws.com/${IMAGENAME_BUILDDATE}.simg"; then
                echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg is now released :)"
                rm -rf $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg
            else 
                echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg is still not released. Something is WRONG"
            fi
        fi
    fi 
done < log.txt

#once everything is uploaded successfully move log file to cvmfs folder, so cvmfs can start downloading the containers:
echo "[Debug] mv logfile to cvmfs directory"
mv log.txt cvmfs

cd cvmfs
echo "[Debug] generate applist.json file for website"
python json_gen.py #this generates the applist.json for the website
# these files will be committed via uses: stefanzweifel/git-auto-commit-action@v4