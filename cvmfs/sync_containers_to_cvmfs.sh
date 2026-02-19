#!/usr/bin/env bash
# set -e

#This script runs on the CVMFS STRATUM 0 server every 5 minutes

#sudo vi /etc/cron.d/sync_containers_to_cvmfs
#*/5 * * * * ec2-user cd ~ && bash /home/rocky/neurocommand/cvmfs/sync_containers_to_cvmfs.sh

#The cronjob logfile gets cleared after every successful run

open_cvmfs_transaction() {
    local repo="$1"
    local output
    output="$(sudo cvmfs_server transaction "$repo" 2>&1)"
    local status=$?

    if [[ $status -eq 0 ]]; then
        [[ -n "$output" ]] && echo "$output"
        return 0
    fi

    if grep -q "another transaction is already open" <<< "$output"; then
        echo "[INFO] Reusing existing open transaction for $repo."
        return 0
    fi

    echo "$output"
    echo "[ERROR] Unable to continue without a CVMFS transaction for $repo."
    exit 2
}

neurocommand_has_upstream_updates() {
    local repo_path="$1"
    local local_head upstream_ref remote_name remote_branch remote_head

    local_head="$(git -C "$repo_path" rev-parse HEAD 2>/dev/null)" || {
        echo "[WARNING] Unable to read local git HEAD in $repo_path. Proceeding with menu rebuild."
        return 0
    }

    upstream_ref="$(git -C "$repo_path" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" || {
        echo "[WARNING] Unable to determine upstream branch in $repo_path. Proceeding with menu rebuild."
        return 0
    }

    remote_name="${upstream_ref%%/*}"
    remote_branch="${upstream_ref#*/}"
    remote_head="$(git -C "$repo_path" ls-remote "$remote_name" "refs/heads/$remote_branch" 2>/dev/null | awk 'NR==1 {print $1}')"

    if [[ -z "$remote_head" ]]; then
        echo "[WARNING] Unable to read remote HEAD for $upstream_ref. Proceeding with menu rebuild."
        return 0
    fi

    if [[ "$local_head" == "$remote_head" ]]; then
        echo "[INFO] neurocommand is already at $upstream_ref ($local_head)."
        return 1
    fi

    echo "[INFO] neurocommand update available: local=$local_head remote=$remote_head ($upstream_ref)."
    return 0
}


LOCKFILE=~/ISRUNNING.lock
if [[ -f $LOCKFILE ]]; then
    echo "there is currently a process running already."
    exit 2
else
    touch $LOCKFILE
    echo "running" >> $LOCKFILE
fi

# echo "Syncing object storages:"
export RCLONE_VERBOSE=2
# rclone copy  nectar:/neurodesk/ aws:/neurodesk

cd ~/neurocommand/

# update application list (the log.txt file gets build in the neurocommand action once all containers are uploaded.):
git pull
cd cvmfs

# check if there is enough free space - otherwise don't do anything:
FREE=`df -k --output=avail / | tail -n1`
if [[ $FREE -lt 100000000 ]]; then               # 100GB = 
    echo "There is not enough free disk space!"
    exit 1
fi;

# download and unpack containers on cvmfs
# curl -s https://raw.githubusercontent.com/NeuroDesk/neurocommand/master/cvmfs/log.txt
# export IMAGENAME_BUILDDATE=fsl_6.0.3_20200905
# export IMAGENAME_BUILDDATE=mrtrix3_3.0.1_20200908
# export IMAGENAME_BUILDDATE=spm12_r7219_20201120
# export LINE='fsl_6.0.4_20210105 categories:functional imaging,structural imaging,diffusion imaging,image segmentation,image registration,'

Field_Separator=$IFS
echo $Field_Separator

declare -A KEEP_IMAGES


while IFS= read -r LINE
do
    echo "LINE: $LINE"
    IMAGENAME_BUILDDATE="$(cut -d' ' -f1 <<< ${LINE})"
    # echo "IMAGENAME_BUILDDATE: $IMAGENAME_BUILDDATE"

    CATEGORIES=`echo $LINE | awk -F"categories:" '{print $2}'`
    # echo "CATEGORIES: $CATEGORIES"

    # echo "check if $IMAGENAME_BUILDDATE is in module files:"
    TOOLNAME="$(cut -d'_' -f1 <<< ${IMAGENAME_BUILDDATE})"
    TOOLVERSION="$(cut -d'_' -f2 <<< ${IMAGENAME_BUILDDATE})"
    BUILDDATE="$(cut -d'_' -f3 <<< ${IMAGENAME_BUILDDATE})"
    # echo "[DEBUG] TOOLNAME: $TOOLNAME"
    # echo "[DEBUG] TOOLVERSION: ${TOOLVERSION}"
    # echo "[DEBUG] BUILDDATE: $BUILDDATE"
    KEEP_IMAGES["$IMAGENAME_BUILDDATE"]=1

    echo "check if $IMAGENAME_BUILDDATE is already on cvmfs:"
    if [[ -f "/cvmfs/neurodesk.ardc.edu.au/containers/$IMAGENAME_BUILDDATE/commands.txt" ]]
    then
        echo "$IMAGENAME_BUILDDATE exists on cvmfs"
    else
        echo "$IMAGENAME_BUILDDATE is not yet on cvmfs."



        
        # check if singularity image is already in object storage
        if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/${IMAGENAME_BUILDDATE}.simg"; then
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg exists in nectar cloud"
            # in case of problems:
            # cvmfs_server check
            # If you get bad whitelist error, check if the repository is signed: sudo /usr/bin/cvmfs_server resign neurodesk.ardc.edu.au
            open_cvmfs_transaction neurodesk.ardc.edu.au

            cd /cvmfs/neurodesk.ardc.edu.au/containers/
            git clone https://github.com/NeuroDesk/transparent-singularity $IMAGENAME_BUILDDATE

            # check if $IMAGENAME_BUILDDATE variable is not empty:
            if [[ -n "$IMAGENAME_BUILDDATE" ]]; then
                cd $IMAGENAME_BUILDDATE
                export SINGULARITY_BINDPATH=/cvmfs
                echo $PATH
                export PATH=$PATH:/usr/sbin/
                ./run_transparent_singularity.sh $IMAGENAME_BUILDDATE --unpack true
            else
                echo "[ERROR] IMAGENAME_BUILDDATE is empty"
                exit 2
            fi
            
            retVal=$?
            if [ $retVal -ne 0 ]; then
                echo "Error in Transparent singularity. Check the log. Aborting!"
                cd ~/temp && cvmfs_server abort 
            else
                cd ~/temp && cvmfs_server publish -m "added $IMAGENAME_BUILDDATE" neurodesk.ardc.edu.au
            fi
        else
            echo "[WARNING] ========================================================="
            echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg does not exist in nectar cloud"
            echo "[WARNING] ========================================================="
        fi
    fi

    # echo "check if custom prompt exists for singularity:"
    # if [[ -f "/cvmfs/neurodesk.ardc.edu.au/containers/${IMAGENAME_BUILDDATE}/${IMAGENAME_BUILDDATE}.simg/.singularity.d/env/99-zz_custom_env.sh" ]]
    # then
    #     echo "99-zz_custom_env exists for ${IMAGENAME_BUILDDATE} on cvmfs"
    # else
    #     echo "99-zz_custom_env does not exist for ${IMAGENAME_BUILDDATE} on cvmfs. Creating it."
    #     CUSTOM_ENV=/.singularity.d/env/99-zz_custom_env.sh
    #     echo "#!/bin/bash" >> $CUSTOM_ENV
    #     PS1="[my_container]\w \$"
    #     EOF
    #         chmod 755 $CUSTOM_ENV
    # fi

    # set internal field separator for the string list
    echo $CATEGORIES
    IFS=','
    for CATEGORY in $CATEGORIES;
    do
        echo $CATEGORY
        CATEGORY="${CATEGORY// /_}"
        MODULE_TARGET_BASE="/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}"

        if [[ -f "/cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}" ]]; then
            if [[ -a "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}" ]]
            then
                echo "$IMAGENAME_BUILDDATE exists in module $CATEGORY"
                echo "Checking if files are up-to-date:"
                FILE1=/cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}
                FILE2=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}
                if cmp --silent -- "$FILE1" "$FILE2"; then
                    echo "files contents are identical"
                else
                    echo "files differ - copy again:"
                    open_cvmfs_transaction neurodesk.ardc.edu.au
                    cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION} /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}
                    cd ~/temp && cvmfs_server publish -m "updating modules for $IMAGENAME_BUILDDATE" neurodesk.ardc.edu.au
                fi
            else
                open_cvmfs_transaction neurodesk.ardc.edu.au
                echo "[DEBUG] mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/"
                mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/
                echo "[DEBUG] cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION} /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}"
                cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION} /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}
                cd ~/temp && cvmfs_server publish -m "added modules for $IMAGENAME_BUILDDATE" neurodesk.ardc.edu.au
                if  [[ -f /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION} ]]; then
                    echo "module file $CATEGORY/$TOOLNAME/${TOOLVERSION} written. This worked!"
                else
                    echo "Something went wrong: cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION} /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}"
                    exit 2
                fi
            fi
        fi


        if [[ -f "/cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua" ]]; then
            if [[ -a "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua" ]]; then
                echo "$IMAGENAME_BUILDDATE exists in module $CATEGORY"
                echo "Checking if files are up-to-date:"
                FILE1=/cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua
                FILE2=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua
                if cmp --silent -- "$FILE1" "$FILE2"; then
                    echo "files contents are identical"
                else
                    echo "files differ - copy again:"
                    open_cvmfs_transaction neurodesk.ardc.edu.au
                    cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua
                    cd ~/temp && cvmfs_server publish -m "updating modules for $IMAGENAME_BUILDDATE" neurodesk.ardc.edu.au
                fi
            else
                open_cvmfs_transaction neurodesk.ardc.edu.au
                echo "[DEBUG] mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/"
                mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/
                echo "[DEBUG] cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua"
                cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua
                cd ~/temp && cvmfs_server publish -m "added modules for $IMAGENAME_BUILDDATE" neurodesk.ardc.edu.au
                if  [[ -f /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua ]]; then
                    echo "module file $CATEGORY/$TOOLNAME/${TOOLVERSION} written. This worked!"
                else
                    echo "Something went wrong: cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua"
                    exit 2
                fi
            fi
        fi
    done
    
    IFS=$Field_Separator

done < /home/rocky/neurocommand/cvmfs/log.txt

# disable unpacked container versions that no longer exist in log.txt:
CONTAINERS_ROOT="/cvmfs/neurodesk.ardc.edu.au/containers"
STALE_IMAGES=()

for CONTAINER_PATH in "$CONTAINERS_ROOT"/*; do
    [[ -d "$CONTAINER_PATH" ]] || continue
    CONTAINER_NAME="$(basename "$CONTAINER_PATH")"

    # Only manage unpacked container directories (skip folders like modules/).
    if [[ ! -f "$CONTAINER_PATH/commands.txt" ]]; then
        continue
    fi

    if [[ -z "${KEEP_IMAGES[$CONTAINER_NAME]+x}" ]]; then
        STALE_IMAGES+=("$CONTAINER_NAME")
    fi
done

if [[ ${#STALE_IMAGES[@]} -eq 0 ]]; then
    echo "[INFO] No stale container directories found to disable."
else
    echo "[INFO] Stale containers not present in log.txt:"
    printf '  - %s\n' "${STALE_IMAGES[@]}"
    echo "[INFO] Module files in /cvmfs/neurodesk.ardc.edu.au/containers/modules are intentionally kept."

    open_cvmfs_transaction neurodesk.ardc.edu.au

    for STALE_IMAGE in "${STALE_IMAGES[@]}"; do
        STALE_CONTAINER_PATH="$CONTAINERS_ROOT/$STALE_IMAGE"
        DOCKER_IMAGE_REF="${STALE_IMAGE%_*}:${STALE_IMAGE##*_}"

        echo "[INFO] Disabling executables in stale container directory: $STALE_CONTAINER_PATH"
        while IFS= read -r -d '' EXECUTABLE_PATH; do
            if [[ ! -w "$EXECUTABLE_PATH" ]]; then
                echo "[INFO] Skipping non-writable executable: $EXECUTABLE_PATH"
                continue
            fi
            cat > "$EXECUTABLE_PATH" << EOF
#!/usr/bin/env bash
echo "This container was disabled due to a known bug or vulnerability. To keep using the software please use a different version. If you absolutely need this container for reproducibility you can pull it from docker hub via the command apptainer pull docker://vnmd/$DOCKER_IMAGE_REF"
EOF
            chmod +x "$EXECUTABLE_PATH"
        done < <(
            find "$STALE_CONTAINER_PATH" \
                \( -type d -name "*.simg" -prune \) -o \
                -type f -perm /111 -print0
        )

        STALE_CONTAINER_IMAGE="$STALE_CONTAINER_PATH/$STALE_IMAGE.simg"
        if [[ -e "$STALE_CONTAINER_IMAGE" ]]; then
            echo "[INFO] Deleting stale container image: $STALE_CONTAINER_IMAGE"
            sudo rm -rf "$STALE_CONTAINER_IMAGE"
        fi
    done

    cd ~ && sudo cvmfs_server publish -m "disabled stale containers not present in log.txt and removed stale .simg files" neurodesk.ardc.edu.au
fi




# update neurocommand installation for the lxde menus:

# to get this to work I manually created these on the CVMFS stratum 0 server:
# sudo mkdir -p /etc/xdg/menus/
# sudo touch /etc/xdg/menus/lxde-applications.menu
# mkdir -p /usr/share/applications/
# mkdir -p /usr/share/desktop-directories/
# sudo touch /usr/share/applications/code.desktop
# sudo touch /usr/share/desktop-directories/lxde-menu-system.directory
# sudo vi /etc/xdg/menus/lxde-applications.menu
#copy content of a real lxde-applications.menu file and save!

NEUROCOMMAND_REPO="/cvmfs/neurodesk.ardc.edu.au/neurocommand"
if neurocommand_has_upstream_updates "$NEUROCOMMAND_REPO"; then
    open_cvmfs_transaction neurodesk.ardc.edu.au
    cd "$NEUROCOMMAND_REPO"
    git pull
    bash build.sh --lxde --edit
    cd ~/temp
    cvmfs_server publish -m "update neurocommand for menus" neurodesk.ardc.edu.au
else
    echo "[INFO] Skipping LXDE menu rebuild/publish; no upstream neurocommand changes detected."
fi

echo "[INFO] Deleting lockfile: $LOCKFILE"
sudo rm -rf "$LOCKFILE"
mv ~/cronjob.log ~/cronjob_previous_run.log


