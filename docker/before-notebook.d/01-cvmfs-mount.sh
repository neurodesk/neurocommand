#!/bin/bash
# Mount CVMFS before the user command runs.
#
# Picked up automatically by the docker-stacks foundation start.sh, which
# executes /usr/local/bin/before-notebook.d/*.sh as root before dropping
# privileges to NB_USER.
#
# Env knobs:
#   NEURODESK_CVMFS_DISABLE=1       skip CVMFS entirely (offline / local-only)
#   NEURODESK_SKIP_REGION_PROBE=1   use the bundled static config without ranking servers

set -e

NB_USER="${NB_USER:-jovyan}"
# neurodesktop's server ranking writes the best servers into the repository config.
SERVER_SELECT="${NEURODESK_CVMFS_SERVER_SELECT:-/opt/neurodesktop/cvmfs_server_select.sh}"

setup_cvmfs() {
    if [ "${NEURODESK_CVMFS_DISABLE:-0}" = "1" ]; then
        echo "[neurocommand] CVMFS disabled by NEURODESK_CVMFS_DISABLE=1"
        return 0
    fi

    if [ ! -e /dev/fuse ]; then
        echo "[neurocommand] /dev/fuse missing - run with --device /dev/fuse (or --privileged). Skipping CVMFS."
        return 0
    fi

    if [ -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
        echo "[neurocommand] CVMFS already mounted."
        return 0
    fi

    CACHE_DIR="/home/${NB_USER}/cvmfs_cache"
    mkdir -p "$CACHE_DIR"
    chown -R cvmfs:root "$CACHE_DIR" 2>/dev/null || true
    chmod 755 "/home/${NB_USER}"

    if [ "${NEURODESK_SKIP_REGION_PROBE:-0}" != "1" ]; then
        if [ -x "$SERVER_SELECT" ]; then
            "$SERVER_SELECT" || echo "[neurocommand] WARN: no CVMFS server measurable, using the fallback server list."
        else
            echo "[neurocommand] WARN: $SERVER_SELECT not found, keeping the bundled config."
        fi
    fi

    echo "[neurocommand] Mounting CVMFS..."
    if [ -x /etc/init.d/autofs ] && service autofs start >/dev/null 2>&1; then
        # A failed readiness probe or status message must trigger the manual mount fallback.
        # shellcheck disable=SC2015
        ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ >/dev/null 2>&1 \
            && echo "[neurocommand] CVMFS ready (autofs)." \
            || mount_cvmfs_manually
    else
        mount_cvmfs_manually
    fi
}

mount_cvmfs_manually() {
    mkdir -p /cvmfs/neurodesk.ardc.edu.au
    if mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au 2>&1; then
        ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ >/dev/null 2>&1 \
            && echo "[neurocommand] CVMFS ready (manual mount)." \
            || echo "[neurocommand] WARN: CVMFS mounted but modules dir not visible yet."
    else
        echo "[neurocommand] WARN: manual CVMFS mount failed. Falling back to local containers."
    fi
}

setup_cvmfs
