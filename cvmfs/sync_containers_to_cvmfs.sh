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

publish_cvmfs_transaction() {
    local repo="$1"
    local message="$2"
    # Publishing remounts the CVMFS repo; ensure this shell is not left in /cvmfs.
    cd "$HOME" || {
        echo "[ERROR] Unable to switch to $HOME before publishing $repo."
        exit 2
    }
    sudo cvmfs_server publish -m "$message" "$repo"
}

abort_cvmfs_transaction() {
    local repo="$1"
    # Keep cwd stable outside /cvmfs before abort/remount operations.
    cd "$HOME" || {
        echo "[ERROR] Unable to switch to $HOME before aborting $repo."
        exit 2
    }
    sudo cvmfs_server abort "$repo"
}

ensure_lxde_menu_prereqs() {
    local repo_path="$1"
    local appmenu="/etc/xdg/menus/lxde-applications.menu"
    local appmenu_dir="/etc/xdg/menus"
    local appdir="/usr/share/applications"
    local deskdir="/usr/share/desktop-directories"
    local fallback_menu=""
    local candidate
    local appmenu_candidates=(
        "$repo_path/test/lxde-applications.menu"
        "$HOME/neurocommand/test/lxde-applications.menu"
    )

    sudo mkdir -p "$appmenu_dir" "$appdir" "$deskdir"

    for candidate in "${appmenu_candidates[@]}"; do
        if [[ -f "$candidate" ]]; then
            fallback_menu="$candidate"
            break
        fi
    done

    if [[ ! -f "$appmenu" ]]; then
        if [[ -n "$fallback_menu" ]]; then
            echo "[INFO] Creating missing LXDE app menu from template: $appmenu"
            sudo cp "$fallback_menu" "$appmenu"
        else
            echo "[WARNING] No LXDE app menu template found; creating minimal $appmenu."
            sudo tee "$appmenu" > /dev/null << 'EOF'
<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"
 "http://www.freedesktop.org/standards/menu-spec/1.0/menu.dtd">

<Menu>
    <Name>Applications</Name>
    <DefaultAppDirs/>
    <DefaultDirectoryDirs/>
    <DefaultMergeDirs/>
</Menu>
EOF
        fi
        sudo chmod 644 "$appmenu"
    fi

    if ! compgen -G "$appdir/*.desktop" > /dev/null; then
        local placeholder_desktop="$appdir/code.desktop"
        echo "[INFO] Creating placeholder desktop entry: $placeholder_desktop"
        sudo tee "$placeholder_desktop" > /dev/null << 'EOF'
[Desktop Entry]
Name=Code
Comment=Placeholder desktop entry for Neurodesk menu generation
Exec=true
Type=Application
Terminal=false
Categories=Utility;
EOF
        sudo chmod 644 "$placeholder_desktop"
    fi

    if ! compgen -G "$deskdir/*.directory" > /dev/null; then
        local placeholder_directory="$deskdir/lxde-menu-system.directory"
        echo "[INFO] Creating placeholder directory entry: $placeholder_directory"
        sudo tee "$placeholder_directory" > /dev/null << 'EOF'
[Desktop Entry]
Name=System
Comment=Placeholder directory entry for Neurodesk menu generation
Type=Directory
Icon=applications-system
EOF
        sudo chmod 644 "$placeholder_directory"
    fi
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

regenerate_log_from_apps_json() {
    local repo_path="$1"
    local generated_log="$repo_path/log.txt"
    local target_log="$repo_path/cvmfs/log.txt"

    echo "[INFO] Regenerating cvmfs/log.txt from neurodesk/apps.json."

    rm -f "$generated_log"

    if ! (cd "$repo_path" && python3 neurodesk/write_log.py); then
        echo "[ERROR] Failed to generate log.txt from neurodesk/apps.json."
        exit 2
    fi

    # Keep the same formatting conventions as the container upload pipeline.
    sed -i '/^$/d' "$generated_log"
    sed -i 's/[][]//g' "$generated_log"
    sed -i -e 's/^[ \t]*//' -e 's/[ \t]*$//' "$generated_log"

    mv -f "$generated_log" "$target_log"
    echo "[INFO] Updated $target_log"
}

commit_log_to_github_if_changed() {
    local repo_path="$1"
    local log_rel_path="cvmfs/log.txt"

    if git -C "$repo_path" diff --quiet -- "$log_rel_path"; then
        echo "[INFO] No changes in $log_rel_path; skipping git commit/push."
        return 0
    fi

    echo "[INFO] Committing regenerated $log_rel_path to GitHub."
    git -C "$repo_path" add "$log_rel_path"

    if [[ -z "$(git -C "$repo_path" config --get user.name)" ]]; then
        git -C "$repo_path" config user.name "neurodesk-cvmfs-bot"
    fi
    if [[ -z "$(git -C "$repo_path" config --get user.email)" ]]; then
        git -C "$repo_path" config user.email "neurodesk-cvmfs-bot@users.noreply.github.com"
    fi

    if ! git -C "$repo_path" commit -m "Regenerate cvmfs/log.txt from apps.json"; then
        echo "[WARNING] Unable to commit $log_rel_path; continuing."
        return 1
    fi

    if ! git -C "$repo_path" push; then
        echo "[WARNING] Initial push failed for $log_rel_path. Attempting git pull --rebase and one push retry."
        if ! git -C "$repo_path" pull --rebase; then
            echo "[WARNING] Rebase failed while retrying push for $log_rel_path; continuing."
            git -C "$repo_path" rebase --abort >/dev/null 2>&1 || true
            return 1
        fi

        if ! git -C "$repo_path" push; then
            echo "[WARNING] Push retry failed for $log_rel_path; continuing."
            return 1
        fi
    fi

    echo "[INFO] Successfully committed and pushed $log_rel_path."
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

NEUROCOMMAND_LOCAL_REPO="$HOME/neurocommand"

cd "$NEUROCOMMAND_LOCAL_REPO"

# Pull latest changes, regenerate log.txt from apps.json, then sync CVMFS from that log.
git pull
regenerate_log_from_apps_json "$NEUROCOMMAND_LOCAL_REPO"
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
                abort_cvmfs_transaction neurodesk.ardc.edu.au
            else
                publish_cvmfs_transaction neurodesk.ardc.edu.au "added $IMAGENAME_BUILDDATE"
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
        # echo $CATEGORY
        CATEGORY="${CATEGORY// /_}"
        MODULE_TARGET_BASE="/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}"

        if [[ -f "/cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}" ]]; then
            if [[ -a "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}" ]]
            then
                # echo "$IMAGENAME_BUILDDATE exists in module $CATEGORY"
                # echo "Checking if files are up-to-date:"
                FILE1=/cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}
                FILE2=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}
                if cmp --silent -- "$FILE1" "$FILE2"; then
                    # echo "files contents are identical"
                else
                    echo "files differ - copy again:"
                    open_cvmfs_transaction neurodesk.ardc.edu.au
                    cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION} /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}
                    publish_cvmfs_transaction neurodesk.ardc.edu.au "updating modules for $IMAGENAME_BUILDDATE"
                fi
            else
                open_cvmfs_transaction neurodesk.ardc.edu.au
                echo "[DEBUG] mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/"
                mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/
                echo "[DEBUG] cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION} /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}"
                cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION} /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}
                publish_cvmfs_transaction neurodesk.ardc.edu.au "added modules for $IMAGENAME_BUILDDATE"
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
                    publish_cvmfs_transaction neurodesk.ardc.edu.au "updating modules for $IMAGENAME_BUILDDATE"
                fi
            else
                open_cvmfs_transaction neurodesk.ardc.edu.au
                echo "[DEBUG] mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/"
                mkdir -p /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/
                echo "[DEBUG] cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua"
                cp /cvmfs/neurodesk.ardc.edu.au/containers/modules/$TOOLNAME/${TOOLVERSION}.lua /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/$CATEGORY/$TOOLNAME/${TOOLVERSION}.lua
                publish_cvmfs_transaction neurodesk.ardc.edu.au "added modules for $IMAGENAME_BUILDDATE"
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

done < "$NEUROCOMMAND_LOCAL_REPO/cvmfs/log.txt"

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
    STALE_CHANGES_MADE=0
    TRANSACTION_OPEN=0

    for STALE_IMAGE in "${STALE_IMAGES[@]}"; do
        STALE_CONTAINER_PATH="$CONTAINERS_ROOT/$STALE_IMAGE"
        STALE_CONTAINER_IMAGE="$STALE_CONTAINER_PATH/$STALE_IMAGE.simg"
        DOCKER_IMAGE_REF="${STALE_IMAGE%_*}:${STALE_IMAGE##*_}"
        CONTAINER_CHANGES_MADE=0
        DISABLE_NOTICE="This container was disabled due to a known bug or vulnerability."
        REPRO_PULL_HINT="docker://vnmd/$DOCKER_IMAGE_REF"

        # If the stale image payload was already removed in an earlier run,
        # skip expensive per-wrapper checks for this container directory.
        if [[ ! -e "$STALE_CONTAINER_IMAGE" ]]; then
            continue
        fi

        while IFS= read -r EXECUTABLE_NAME; do
            [[ -n "$EXECUTABLE_NAME" ]] || continue
            EXECUTABLE_PATH="$STALE_CONTAINER_PATH/$EXECUTABLE_NAME"

            # Only disable top-level command wrappers generated from commands.txt.
            if [[ ! -f "$EXECUTABLE_PATH" || ! -x "$EXECUTABLE_PATH" ]]; then
                continue
            fi

            if grep -Fq "$DISABLE_NOTICE" "$EXECUTABLE_PATH" 2>/dev/null && \
               grep -Fq "$REPRO_PULL_HINT" "$EXECUTABLE_PATH" 2>/dev/null; then
                continue
            fi

            if [[ $TRANSACTION_OPEN -eq 0 ]]; then
                open_cvmfs_transaction neurodesk.ardc.edu.au
                TRANSACTION_OPEN=1
            fi

            if [[ ! -w "$EXECUTABLE_PATH" ]]; then
                echo "[WARNING] Unable to disable non-writable wrapper: $EXECUTABLE_PATH"
                continue
            fi

            if [[ $CONTAINER_CHANGES_MADE -eq 0 ]]; then
                echo "[INFO] Disabling executables in stale container directory: $STALE_CONTAINER_PATH"
            fi

            cat > "$EXECUTABLE_PATH" << EOF
#!/usr/bin/env bash
echo "This container was disabled due to a known bug or vulnerability. To keep using the software please use a different version. If you absolutely need this container for reproducibility you can pull it from docker hub via the command apptainer pull docker://vnmd/$DOCKER_IMAGE_REF"
EOF
            chmod +x "$EXECUTABLE_PATH"
            CONTAINER_CHANGES_MADE=1
            STALE_CHANGES_MADE=1
        done < "$STALE_CONTAINER_PATH/commands.txt"

        if [[ -e "$STALE_CONTAINER_IMAGE" ]]; then
            if [[ $TRANSACTION_OPEN -eq 0 ]]; then
                open_cvmfs_transaction neurodesk.ardc.edu.au
                TRANSACTION_OPEN=1
            fi
            echo "[INFO] Deleting stale container image: $STALE_CONTAINER_IMAGE"
            sudo rm -rf "$STALE_CONTAINER_IMAGE"
            CONTAINER_CHANGES_MADE=1
            STALE_CHANGES_MADE=1
        fi

    done

    if [[ $STALE_CHANGES_MADE -eq 1 ]]; then
        publish_cvmfs_transaction neurodesk.ardc.edu.au "disabled stale containers not present in log.txt and removed stale .simg files"
    else
        echo "[INFO] No stale container changes detected; skipping publish."
    fi
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
    ensure_lxde_menu_prereqs "$NEUROCOMMAND_REPO"
    open_cvmfs_transaction neurodesk.ardc.edu.au
    # Run repo updates in a subshell so the parent shell never keeps cwd in /cvmfs.
    if (cd "$NEUROCOMMAND_REPO" && git pull && bash build.sh --lxde --edit); then
        publish_cvmfs_transaction neurodesk.ardc.edu.au "update neurocommand for menus"
    else
        echo "[ERROR] LXDE menu rebuild failed; aborting CVMFS transaction."
        abort_cvmfs_transaction neurodesk.ardc.edu.au
    fi
else
    echo "[INFO] Skipping LXDE menu rebuild/publish; no upstream neurocommand changes detected."
fi

commit_log_to_github_if_changed "$NEUROCOMMAND_LOCAL_REPO"

echo "[INFO] Deleting lockfile: $LOCKFILE"
sudo rm -rf "$LOCKFILE"
mv ~/cronjob.log ~/cronjob_previous_run.log
