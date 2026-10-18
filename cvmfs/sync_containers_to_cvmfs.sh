#!/usr/bin/env bash
# set -e

#This script runs on the CVMFS STRATUM 0 server every 5 minutes

#sudo vi /etc/cron.d/sync_containers_to_cvmfs
#*/5 * * * * ec2-user cd ~ && bash /home/rocky/neurocommand/cvmfs/sync_containers_to_cvmfs.sh

#The cronjob logfile is rotated after every run that reaches the end

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
    if ! sudo cvmfs_server publish -m "$message" "$repo"; then
        echo "[ERROR] CVMFS publication failed; stopping before retirement."
        abort_cvmfs_transaction "$repo"
        exit 2
    fi
}

abort_cvmfs_transaction() {
    local repo="$1"
    # Keep cwd stable outside /cvmfs before abort/remount operations.
    cd "$HOME" || {
        echo "[ERROR] Unable to switch to $HOME before aborting $repo."
        exit 2
    }
    # -f keeps this non-interactive: without it the y/N prompt reads from this
    # script's stdin (log.txt inside the sync loop), eating a container line
    # and leaving the transaction open.
    sudo cvmfs_server abort -f "$repo" || exit 2
}

ensure_nested_catalog_markers_for_container() {
    local container_dir="$1"
    local container_name
    local simg_dir
    local catalog_dir

    container_name="$(basename "$container_dir")"
    simg_dir="$container_dir/$container_name.simg"

    # Keep the nested catalogs explicit. The publish step has proven unreliable
    # at creating these marker files automatically in directories owned by the
    # sync user.
    for catalog_dir in \
        "$container_dir" \
        "$simg_dir" \
        "$simg_dir/usr" \
        "$simg_dir/usr/lib" \
        "$simg_dir/usr/share"; do
        if [[ ! -d "$catalog_dir" ]]; then
            continue
        fi

        if [[ -f "$catalog_dir/.cvmfscatalog" ]]; then
            continue
        fi

        if ! sudo touch "$catalog_dir/.cvmfscatalog"; then
            echo "[ERROR] Failed to create nested catalog marker: $catalog_dir/.cvmfscatalog"
            return 1
        fi

        echo "[INFO] Created nested catalog marker: $catalog_dir/.cvmfscatalog"
    done

    return 0
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

sync_checkout_to_origin() {
    local repo_path="$1"
    local upstream_ref discarded

    upstream_ref="$(git -C "$repo_path" rev-parse --abbrev-ref --symbolic-full-name '@{u}')" || return 1
    GIT_TERMINAL_PROMPT=0 git -C "$repo_path" fetch --prune "${upstream_ref%%/*}" || return 1

    discarded="$(git -C "$repo_path" log --oneline "${upstream_ref}..HEAD")"
    if [[ -n "$discarded" ]]; then
        echo "[WARNING] Discarding local commits in $repo_path:"
        echo "$discarded"
    fi
    git -C "$repo_path" reset --hard "$upstream_ref"
}

git_pull_rebase_non_interactive() {
    local repo_path="$1"

    if ! GIT_TERMINAL_PROMPT=0 \
         GIT_MERGE_AUTOEDIT=no \
         GIT_EDITOR=true \
         GIT_SEQUENCE_EDITOR=true \
         git -C "$repo_path" pull --rebase --autostash; then
        echo "[WARNING] Non-interactive git pull --rebase failed in $repo_path."
        git -C "$repo_path" rebase --abort >/dev/null 2>&1 || true
        return 1
    fi

    return 0
}

# A failed install is retried after INSTALL_RETRY_MINUTES, or sooner when the
# transparent-singularity scripts change, instead of on every cron run.
INSTALL_FAILURE_DIR="$HOME/.cache/neurodesk-sync/failed-installs"
INSTALL_RETRY_MINUTES=360

installer_revision() {
    git -C "$NEUROCOMMAND_LOCAL_REPO" rev-parse HEAD:neurodesk/transparent-singularity
}

install_recently_failed() {
    local marker="$INSTALL_FAILURE_DIR/$1"
    [[ -f "$marker" ]] &&
        [[ -z "$(find "$marker" -mmin +"$INSTALL_RETRY_MINUTES")" ]] &&
        [[ "$(cat "$marker")" == "$(installer_revision)" ]]
}

# Keep earlier builds of a tool whose new build is not installed: retiring them
# could leave the tool with no working version. Versions may contain
# underscores but recipe names cannot, so key on the recipe name. This also
# protects named variants such as <recipe>_arm64, which only delays retirement.
protect_tool_builds() {
    FAILED_TOOLS["${1%%_*}"]=1
}

mark_install_failed() {
    INSTALL_FAILED=1
    protect_tool_builds "$1"
}

record_install_failure() {
    mark_install_failed "$1"
    mkdir -p "$INSTALL_FAILURE_DIR" && installer_revision > "$INSTALL_FAILURE_DIR/$1"
}

LOCKFILE=~/ISRUNNING.lock
if [[ -f $LOCKFILE ]]; then
    echo "there is currently a process running already."
    exit 2
else
    touch $LOCKFILE
    echo "running" >> $LOCKFILE
fi

cleanup_lockfile() {
    if [[ -f "$LOCKFILE" ]]; then
        echo "[INFO] Deleting lockfile: $LOCKFILE"
        sudo rm -f -- "$LOCKFILE"
    fi
}

# Every exit after acquiring the lock must release it. In particular, a failed
# reconciliation preflight must not disable all later cron runs.
trap cleanup_lockfile EXIT

# echo "Syncing object storages:"
export RCLONE_VERBOSE=2
# rclone copy  nectar:/neurodesk/ aws:/neurodesk

NEUROCOMMAND_LOCAL_REPO="$HOME/neurocommand"

cd "$NEUROCOMMAND_LOCAL_REPO" || exit

# Mirror main exactly; this checkout carries no local work. Install what
# apps.json describes rather than the published cvmfs/log.txt, which the upload
# workflow advances later: syncing from an older inventory would disable newer
# installations. The regenerated log is local and discarded by the next reset.
CLEANUP_ALLOWED=1
declare -A KEEP_IMAGES=()
if ! sync_checkout_to_origin "$NEUROCOMMAND_LOCAL_REPO"; then
    CLEANUP_ALLOWED=0
    echo "[WARNING] Continuing with the current checkout because fetching origin failed."
fi
if PUBLIC_PROTECTION=$(python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/retention_inventory.py" \
    --repo "$NEUROCOMMAND_LOCAL_REPO" --revision HEAD); then
    while IFS= read -r image; do
        KEEP_IMAGES["$image"]=1
    done <<< "$PUBLIC_PROTECTION"
else
    CLEANUP_ALLOWED=0
fi
if ! python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/generate_metadata.py"; then
    echo "[ERROR] Failed to generate cvmfs/log.txt from neurodesk/apps.json."
    exit 2
fi
cd cvmfs || exit

# check if there is enough free space - otherwise don't do anything:
FREE=$(df -k --output=avail / | tail -n1)
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

INSTALL_FAILED=0
INSTALL_DEFERRED=0
declare -A FAILED_TOOLS=()


while IFS= read -r LINE
do
    # echo "LINE: $LINE"
    IMAGENAME_BUILDDATE="$(cut -d' ' -f1 <<< "${LINE}")"
    # echo "IMAGENAME_BUILDDATE: $IMAGENAME_BUILDDATE"

    KEEP_IMAGES["$IMAGENAME_BUILDDATE"]=1

    # echo "check if $IMAGENAME_BUILDDATE is already on cvmfs:"
    if [[ -f "/cvmfs/neurodesk.ardc.edu.au/containers/$IMAGENAME_BUILDDATE/commands.txt" ]]
    then
        echo "$IMAGENAME_BUILDDATE exists on cvmfs"
    elif install_recently_failed "$IMAGENAME_BUILDDATE"; then
        echo "[WARNING] Skipping $IMAGENAME_BUILDDATE: its install failed less than $INSTALL_RETRY_MINUTES minutes ago with the current transparent-singularity scripts."
        protect_tool_builds "$IMAGENAME_BUILDDATE"
        INSTALL_DEFERRED=1
    else
        echo "$IMAGENAME_BUILDDATE is not yet on cvmfs."

        # transparent-singularity executes commands inside the unpacked image
        # to generate wrappers. Ensure a foreign-architecture image can run on
        # this publisher before opening a transaction or downloading the SIF.
        if ! "$NEUROCOMMAND_LOCAL_REPO/cvmfs/ensure_binfmt.sh" "$IMAGENAME_BUILDDATE"; then
            echo "[ERROR] Cannot deploy $IMAGENAME_BUILDDATE without working QEMU/binfmt support."
            mark_install_failed "$IMAGENAME_BUILDDATE"
            continue
        fi

        # Let transparent-singularity try every supported backend. A Nectar-only
        # preflight would skip images that are available from S3 or a registry.
        open_cvmfs_transaction neurodesk.ardc.edu.au

        if ! cd /cvmfs/neurodesk.ardc.edu.au/containers/; then
            echo "[ERROR] Cannot enter the CVMFS containers directory. Aborting transaction."
            abort_cvmfs_transaction neurodesk.ardc.edu.au
            mark_install_failed "$IMAGENAME_BUILDDATE"
            continue
        fi

        # The outer commands.txt check already skipped completed installs, so
        # anything here is an incomplete install and safe to remove.
        if [[ -e "$IMAGENAME_BUILDDATE" ]]; then
            echo "[WARNING] Removing stale leftover directory from a previous failed run: $IMAGENAME_BUILDDATE"
            sudo rm -rf "$IMAGENAME_BUILDDATE"
        fi

        # Use the scripts from this exact neurocommand checkout. Cloning the
        # separate transparent-singularity repository bypasses fixes tested here.
        if ! mkdir -p "$IMAGENAME_BUILDDATE" || \
           ! cp -a "$NEUROCOMMAND_LOCAL_REPO/neurodesk/transparent-singularity/." "$IMAGENAME_BUILDDATE/"; then
            echo "[ERROR] Failed to stage transparent-singularity for $IMAGENAME_BUILDDATE. Aborting transaction."
            abort_cvmfs_transaction neurodesk.ardc.edu.au
            mark_install_failed "$IMAGENAME_BUILDDATE"
            continue
        fi

        # check if $IMAGENAME_BUILDDATE variable is not empty:
        if [[ -n "$IMAGENAME_BUILDDATE" ]]; then
            if ! cd "$IMAGENAME_BUILDDATE"; then
                echo "[ERROR] Cannot enter container directory: $IMAGENAME_BUILDDATE. Aborting transaction."
                abort_cvmfs_transaction neurodesk.ardc.edu.au
                mark_install_failed "$IMAGENAME_BUILDDATE"
                continue
            fi
            export SINGULARITY_BINDPATH=/cvmfs
            echo "$PATH"
            export PATH=$PATH:/usr/sbin/
            # stdin is log.txt inside this loop; keep child processes from consuming it
            ./run_transparent_singularity.sh "$IMAGENAME_BUILDDATE" --unpack true < /dev/null
            retVal=$?

            if [[ $retVal -eq 0 ]]; then
                ensure_nested_catalog_markers_for_container "/cvmfs/neurodesk.ardc.edu.au/containers/$IMAGENAME_BUILDDATE" || retVal=$?
            fi
        else
            echo "[ERROR] IMAGENAME_BUILDDATE is empty"
            exit 2
        fi

        if [ $retVal -ne 0 ]; then
            echo "Error in Transparent singularity. Check the log. Aborting!"
            abort_cvmfs_transaction neurodesk.ardc.edu.au
            record_install_failure "$IMAGENAME_BUILDDATE"
        else
            publish_cvmfs_transaction neurodesk.ardc.edu.au "added $IMAGENAME_BUILDDATE"
            rm -f "$INSTALL_FAILURE_DIR/$IMAGENAME_BUILDDATE"
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

done < "$NEUROCOMMAND_LOCAL_REPO/cvmfs/log.txt"

echo "[INFO] Checking that module files point to the latest kept container builds."
if python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/reconcile_module_files.py" \
    --repo-root /cvmfs/neurodesk.ardc.edu.au \
    --log "$NEUROCOMMAND_LOCAL_REPO/cvmfs/log.txt" \
    --check; then
    RECONCILE_STATUS=0
else
    RECONCILE_STATUS=$?
fi

if [[ $RECONCILE_STATUS -eq 1 ]]; then
    echo "[INFO] Reconciling canonical and public module files before stale containers are disabled."
    open_cvmfs_transaction neurodesk.ardc.edu.au
    if python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/reconcile_module_files.py" \
        --repo-root /cvmfs/neurodesk.ardc.edu.au \
        --log "$NEUROCOMMAND_LOCAL_REPO/cvmfs/log.txt"; then
        publish_cvmfs_transaction neurodesk.ardc.edu.au "reconciled module files to latest kept containers"
    else
        echo "[ERROR] Module reconciliation failed. Aborting CVMFS transaction."
        abort_cvmfs_transaction neurodesk.ardc.edu.au
        exit 2
    fi
elif [[ $RECONCILE_STATUS -eq 0 ]]; then
    echo "[INFO] Module files are already reconciled."
else
    echo "[ERROR] Module reconciliation check failed."
    exit 2
fi

# Bundles reuse already-published independent containers and have no image.
bundle_args=(--manifest "$NEUROCOMMAND_LOCAL_REPO/neurodesk/bundles.json"
    --catalog "$NEUROCOMMAND_LOCAL_REPO/neurodesk/apps.json"
    --module-root /cvmfs/neurodesk.ardc.edu.au/containers/modules
    --public-root /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules --require-installed)
if python3 "$NEUROCOMMAND_LOCAL_REPO/neurodesk/transparent-singularity/artifact_renderer.py" "${bundle_args[@]}" --check; then
    BUNDLE_STATUS=0
else
    BUNDLE_STATUS=$?
fi
if [[ $BUNDLE_STATUS -eq 1 ]]; then
    open_cvmfs_transaction neurodesk.ardc.edu.au
    if ! python3 "$NEUROCOMMAND_LOCAL_REPO/neurodesk/transparent-singularity/artifact_renderer.py" "${bundle_args[@]}" ||
        ! publish_cvmfs_transaction neurodesk.ardc.edu.au "published versioned tool bundles"; then
        abort_cvmfs_transaction neurodesk.ardc.edu.au
        exit 2
    fi
elif [[ $BUNDLE_STATUS -ne 0 ]]; then
    exit 2
fi

UPSTREAM=$(git -C "$NEUROCOMMAND_LOCAL_REPO" rev-parse --abbrev-ref --symbolic-full-name '@{u}') || CLEANUP_ALLOWED=0
if [[ $CLEANUP_ALLOWED -eq 1 ]] &&
    GIT_TERMINAL_PROMPT=0 git -C "$NEUROCOMMAND_LOCAL_REPO" fetch --prune "${UPSTREAM%%/*}" &&
    PUBLIC_PROTECTION=$(python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/retention_inventory.py" \
        --repo "$NEUROCOMMAND_LOCAL_REPO" --revision "$UPSTREAM"); then
    while IFS= read -r image; do
        KEEP_IMAGES["$image"]=1
    done <<< "$PUBLIC_PROTECTION"
else
    echo "[ERROR] Cannot refresh public inventory; retirement skipped."
    exit 2
fi

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
    if ! REFERENCED_CONTAINER_NAMES=$(python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/reconcile_module_files.py" \
        --repo-root /cvmfs/neurodesk.ardc.edu.au --referenced-containers); then
        echo "[ERROR] Cannot scan module references; stale container cleanup stopped." >&2
        exit 2
    fi
    declare -A REFERENCED_CONTAINERS=()
    while IFS= read -r REFERENCED_IMAGE; do
        [[ -n "$REFERENCED_IMAGE" ]] || continue
        REFERENCED_CONTAINERS["$REFERENCED_IMAGE"]=1
    done <<< "$REFERENCED_CONTAINER_NAMES"
    STALE_CHANGES_MADE=0
    TRANSACTION_OPEN=0

    for STALE_IMAGE in "${STALE_IMAGES[@]}"; do
        if [[ -n "${REFERENCED_CONTAINERS[$STALE_IMAGE]+x}" ]]; then
            echo "[INFO] Preserving stale container referenced by a module: $STALE_IMAGE"
            continue
        fi
        if [[ -n "${FAILED_TOOLS[${STALE_IMAGE%%_*}]+x}" ]]; then
            echo "[INFO] Preserving stale container while a newer build failed to install: $STALE_IMAGE"
            continue
        fi
        STALE_CONTAINER_PATH="$CONTAINERS_ROOT/$STALE_IMAGE"
        STALE_CONTAINER_IMAGE="$STALE_CONTAINER_PATH/$STALE_IMAGE.simg"
        DOCKER_IMAGE_REF="${STALE_IMAGE%_*}:${STALE_IMAGE##*_}"
        CONTAINER_CHANGES_MADE=0
        DISABLED_WRAPPER="$NEUROCOMMAND_LOCAL_REPO/cvmfs/write_disabled_wrapper.sh"

        # If the stale image payload was already removed in an earlier run and
        # every wrapper is already in the current disabled form, skip the
        # per-wrapper loop. Wrappers disabled before they exited non-zero are
        # rewritten even though their image is gone.
        if [[ ! -e "$STALE_CONTAINER_IMAGE" ]]; then
            OUTDATED_WRAPPER=0
            while IFS= read -r EXECUTABLE_NAME; do
                EXECUTABLE_PATH="$STALE_CONTAINER_PATH/$EXECUTABLE_NAME"
                if [[ -n "$EXECUTABLE_NAME" && -f "$EXECUTABLE_PATH" && -x "$EXECUTABLE_PATH" ]] && \
                   ! "$DISABLED_WRAPPER" --check "$EXECUTABLE_PATH"; then
                    OUTDATED_WRAPPER=1
                    break
                fi
            done < "$STALE_CONTAINER_PATH/commands.txt"
            if [[ $OUTDATED_WRAPPER -eq 0 ]]; then
                continue
            fi
        fi

        while IFS= read -r EXECUTABLE_NAME; do
            [[ -n "$EXECUTABLE_NAME" ]] || continue
            EXECUTABLE_PATH="$STALE_CONTAINER_PATH/$EXECUTABLE_NAME"

            # Only disable top-level command wrappers generated from commands.txt.
            if [[ ! -f "$EXECUTABLE_PATH" || ! -x "$EXECUTABLE_PATH" ]]; then
                continue
            fi

            if "$DISABLED_WRAPPER" --check "$EXECUTABLE_PATH" 2>/dev/null; then
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

            "$DISABLED_WRAPPER" "$EXECUTABLE_PATH" "$DOCKER_IMAGE_REF"
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

# Existing commands.txt files make completed containers immutable to the normal
# install loop above. Reconcile XAUTHORITY forwarding and NVIDIA defaults
# without downloading or unpacking their container images again.
WRAPPER_RECONCILE_STATUS=0
python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/reconcile_wrapper_xauthority.py" \
    --repo-root /cvmfs/neurodesk.ardc.edu.au \
    --check || WRAPPER_RECONCILE_STATUS=$?

if [[ $WRAPPER_RECONCILE_STATUS -eq 1 ]]; then
    echo "[INFO] Reconciling container wrapper XAUTHORITY forwarding and NVIDIA defaults."
    open_cvmfs_transaction neurodesk.ardc.edu.au

    if python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/reconcile_wrapper_xauthority.py" \
        --repo-root /cvmfs/neurodesk.ardc.edu.au && \
       python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/reconcile_wrapper_xauthority.py" \
        --repo-root /cvmfs/neurodesk.ardc.edu.au \
        --check; then
        if ! publish_cvmfs_transaction neurodesk.ardc.edu.au "reconciled container wrapper XAUTHORITY forwarding and NVIDIA defaults"; then
            echo "[ERROR] Failed to publish reconciled container wrappers. Aborting CVMFS transaction."
            abort_cvmfs_transaction neurodesk.ardc.edu.au
            exit 2
        fi
    else
        echo "[ERROR] Wrapper reconciliation failed verification. Aborting CVMFS transaction."
        abort_cvmfs_transaction neurodesk.ardc.edu.au
        exit 2
    fi
elif [[ $WRAPPER_RECONCILE_STATUS -eq 0 ]]; then
    echo "[INFO] Container wrapper XAUTHORITY forwarding and NVIDIA defaults are up to date."
else
    echo "[ERROR] Wrapper reconciliation preflight failed."
    exit 2
fi

# Reconcile startup bundles even for images installed by an earlier sync.
prefetch_args=(--repo-root /cvmfs/neurodesk.ardc.edu.au
    --log "$NEUROCOMMAND_LOCAL_REPO/cvmfs/log.txt")
if python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/prefetch_filebundles.py" "${prefetch_args[@]}" --check; then
    PREFETCH_STATUS=0
else
    PREFETCH_STATUS=$?
fi
if [[ $PREFETCH_STATUS -eq 1 ]]; then
    open_cvmfs_transaction neurodesk.ardc.edu.au
    if python3 "$NEUROCOMMAND_LOCAL_REPO/cvmfs/prefetch_filebundles.py" "${prefetch_args[@]}"; then
        publish_cvmfs_transaction neurodesk.ardc.edu.au "published FSL startup file bundle"
    else
        echo "[ERROR] Startup bundle publication failed. Aborting CVMFS transaction."
        abort_cvmfs_transaction neurodesk.ardc.edu.au
        exit 2
    fi
elif [[ $PREFETCH_STATUS -ne 0 ]]; then
    exit 2
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
    if (git_pull_rebase_non_interactive "$NEUROCOMMAND_REPO" && cd "$NEUROCOMMAND_REPO" && bash build.sh --lxde --edit); then
        publish_cvmfs_transaction neurodesk.ardc.edu.au "update neurocommand for menus"
    else
        echo "[ERROR] LXDE menu rebuild failed; aborting CVMFS transaction."
        abort_cvmfs_transaction neurodesk.ardc.edu.au
    fi
else
    echo "[INFO] Skipping LXDE menu rebuild/publish; no upstream neurocommand changes detected."
fi

if ! cleanup_lockfile; then
    echo "[ERROR] Failed to delete lockfile: $LOCKFILE"
    exit 2
fi
trap - EXIT
# Runs that only skip backed-off installs keep the failed attempt's log.
if [[ $INSTALL_FAILED -ne 0 ]]; then
    echo "[ERROR] At least one container failed to install; see the errors above."
    mv ~/cronjob.log ~/cronjob_failed_run.log
    exit 2
fi
mv ~/cronjob.log ~/cronjob_previous_run.log
if [[ $INSTALL_DEFERRED -ne 0 ]]; then
    exit 2
fi
