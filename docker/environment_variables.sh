#!/bin/bash
# Sourced from /etc/profile.d/ in interactive shells, and from /etc/bash.bashrc
# so non-login bash invocations (jupyter kernels, `bash -c ...`) also see it.
# Re-runs MODULEPATH/CVMFS detection on every source so new shells pick up
# CVMFS after a deferred mount completes.

if [ -z "$NEURODESK_ENV_SOURCED" ]; then
    export NEURODESK_ENV_SOURCED=1
    : "${NB_USER:=${USER}}"
    : "${USER:=${NB_USER}}"
    export NB_USER USER
fi

export NEURODESKTOP_LOCAL_CONTAINERS="${NEURODESKTOP_LOCAL_CONTAINERS:-/neurodesktop-storage/containers}"
export OFFLINE_MODULES=${NEURODESKTOP_LOCAL_CONTAINERS}/modules/
export CVMFS_MODULES=/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/

# Nudge autofs so a lazily-mounted CVMFS becomes visible to the `-d` check.
ls "$CVMFS_MODULES" >/dev/null 2>&1 || true

# Each <category> subdir of CVMFS_MODULES becomes its own MODULEPATH entry so
# Lmod presents modules as `<tool>/<version>` (transparent-singularity layout).
if [ -d "$CVMFS_MODULES" ]; then
    cvmfs_expanded=$(echo ${CVMFS_MODULES}* | sed 's/ /:/g')
    if [ -d "$OFFLINE_MODULES" ]; then
        export MODULEPATH=${OFFLINE_MODULES}:${cvmfs_expanded}
    else
        export MODULEPATH=${cvmfs_expanded}
    fi
    export CVMFS_DISABLE=false
    unset cvmfs_expanded
else
    export MODULEPATH=${OFFLINE_MODULES}
    export CVMFS_DISABLE=true
fi

# Apptainer needs to see host-side dirs that contain user data and the CVMFS tree.
export APPTAINER_BINDPATH=/data,/mnt,/neurodesktop-storage,/tmp,/cvmfs
export APPTAINERENV_SUBJECTS_DIR=${HOME}/freesurfer-subjects-dir
export MPLCONFIGDIR=${HOME}/.config/matplotlib-mpldir

# One-time interactive banner.
if [ -z "$NEURODESK_MSG_SHOWN" ] && [ -f '/usr/share/module.sh' ] && [[ $- == *i* || -t 1 ]]; then
    export NEURODESK_MSG_SHOWN=1
    if [ -d "${OFFLINE_MODULES}" ] && [ -d "${CVMFS_MODULES}" ]; then
        echo "Local containers in $OFFLINE_MODULES take priority over CVMFS."
    fi
    echo 'Run "ml av" to list available modules, then "ml <tool>/<version>" to load one.'
    if [[ "$CVMFS_DISABLE" == "true" ]]; then
        echo "CVMFS not yet available. Falling back to ${OFFLINE_MODULES} (CVMFS picked up automatically once mounted)."
        [ ! -d "${OFFLINE_MODULES}" ] && echo "No local containers downloaded yet."
    fi
fi
