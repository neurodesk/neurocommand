#!/bin/bash -i

# fetch_and_run.sh [name] [version] {cmd} {args}
# Example:
#   fetch_and_run.sh itksnap 3.8.0 itksnap-wt
#
# Legacy (with builddate, still supported):
#   fetch_and_run.sh itksnap 3.8.0 20200505 itksnap-wt

# source ~/.bashrc
_script="$(readlink -f "${BASH_SOURCE[0]}")" ## who am i? ##
_base="$(dirname "$_script")" ## Delete last component from $_script ##
echo "[INFO] fetch_and_run.sh line $LINENO: Script name : $_script"
echo "[INFO] fetch_and_run.sh line $LINENO: Current working dir : $PWD"
echo "[INFO] fetch_and_run.sh line $LINENO: Script location path (dir) : $_base"
echo "[CHECK] fetch_and_run.sh line $LINENO: SINGULARITY_BINDPATH : $SINGULARITY_BINDPATH"

# -z checks if SINGULARITY_BINDPATH is not set
if [ -z "$SINGULARITY_BINDPATH" ]
then
        echo "[WARNING] fetch_and_run.sh line $LINENO: SINGULARITY_BINDPATH is not set. Trying to set it"
        export SINGULARITY_BINDPATH="$PWD"

        #   if /cvmfs exists add this as well:
        if [ -d "/cvmfs" ]; then
            export SINGULARITY_BINDPATH="$SINGULARITY_BINDPATH",/cvmfs
        fi
        echo "[CHECK] fetch_and_run.sh line $LINENO: SINGULARITY_BINDPATH : $SINGULARITY_BINDPATH"
fi

# shellcheck disable=SC1091
source "${_base}"/configparser.sh "${_base}"/config.ini

# Resolve builddate from apps.json for a given module name and version
resolve_builddate() {
    local mod_name="$1"
    local mod_vers="$2"
    local apps_json="${_base}/apps.json"
    if [[ -f "$apps_json" ]]; then
        python3 -c "
import json, sys
with open('${apps_json}') as f:
    data = json.load(f)
for menu_name, menu_data in data.items():
    for app_name, app_data in menu_data.get('apps', {}).items():
        if app_name == '${mod_name} ${mod_vers}':
            print(app_data.get('version', ''))
            sys.exit(0)
print('')
"
    else
        echo ""
    fi
}

MOD_NAME=$1
MOD_VERS=$2

# Backward compatibility: if $3 looks like a builddate (8-digit number), consume it
# Otherwise $3 onwards are exec + user args
if [[ "$3" =~ ^[0-9]{8}$ ]]; then
    MOD_DATE=$3
    shift 3
else
    MOD_DATE=""
    shift 2
fi

echo "[INFO] fetch_and_run.sh line $LINENO: MOD_NAME: $MOD_NAME"
echo "[INFO] fetch_and_run.sh line $LINENO: MOD_VERS: $MOD_VERS"
echo "[INFO] fetch_and_run.sh line $LINENO: MOD_DATE: $MOD_DATE (empty = resolve via module system)"

# This is to capture legacy use. If CVMFS_DISABLE is not set, we assume it is false, which was the legacy behaviour.
if [ -z "$CVMFS_DISABLE" ]; then
    export CVMFS_DISABLE="false"
fi

# Set up module paths for both CVMFS and local containers
if [[ "$CVMFS_DISABLE" == "false" ]]; then
    CVMFS_MODS_PATH="/cvmfs/neurodesk.ardc.edu.au/containers/modules"
    LOCAL_MODS_PATH="${_base}/containers/modules"
    if [[ -d "$CVMFS_MODS_PATH" ]]; then
        MODS_PATH="${LOCAL_MODS_PATH}:${CVMFS_MODS_PATH}"
    else
        echo "[WARNING] fetch_and_run.sh line $LINENO: CVMFS module path not found, falling back to local only."
        CVMFS_DISABLE=true
        MODS_PATH="${LOCAL_MODS_PATH}"
    fi
else
    MODS_PATH="${_base}/containers/modules"
fi
module use ${MODS_PATH}

# Check if the module is available
if ! module avail "${MOD_NAME}/${MOD_VERS}" 2>&1 | grep -q "${MOD_NAME}/${MOD_VERS}"; then
    echo "[WARNING] fetch_and_run.sh line $LINENO: Module ${MOD_NAME}/${MOD_VERS} not found. Attempting to download container."

    # Resolve builddate from apps.json if not provided
    if [[ -z "$MOD_DATE" ]]; then
        MOD_DATE=$(resolve_builddate "$MOD_NAME" "$MOD_VERS")
        echo "[INFO] fetch_and_run.sh line $LINENO: Resolved builddate from apps.json: $MOD_DATE"
    fi

    if [[ -z "$MOD_DATE" ]]; then
        echo "[ERROR] fetch_and_run.sh line $LINENO: Could not resolve builddate for ${MOD_NAME} ${MOD_VERS}. Cannot download container."
        read -n 1 -s -r -p "Press any key to exit..."
        exit 2
    fi

    # Download the container
    export CONTAINER_PATH="${_base}"/containers
    # shellcheck disable=SC1091
    source "${_base}"/fetch_containers.sh "$MOD_NAME" "$MOD_VERS" "$MOD_DATE"
    module use ${MODS_PATH}
fi

# Load the module - this prepends the container directory to PATH
echo "[INFO] fetch_and_run.sh line $LINENO: Loading module ${MOD_NAME}/${MOD_VERS}"
module load "${MOD_NAME}/${MOD_VERS}"

# Extract the container directory from PATH (it was just prepended by module load)
CONTAINER_DIR=$(echo "$PATH" | tr ':' '\n' | head -1)
CONTAINER_DIR_NAME=$(basename "$CONTAINER_DIR")
CONTAINER_FILE_NAME="${CONTAINER_DIR}/${CONTAINER_DIR_NAME}.simg"

echo "[INFO] fetch_and_run.sh line $LINENO: Container resolved to: $CONTAINER_FILE_NAME"
echo "[INFO] fetch_and_run.sh line $LINENO: Module '${MOD_NAME}/${MOD_VERS}' is installed. Use the command 'module load ${MOD_NAME}/${MOD_VERS}' outside of this shell to use it."

# If no additional command -> Give user a shell in the image
if [ $# -eq 0 ]; then
    echo "[INFO] fetch_and_run.sh line $LINENO: looking for ${CONTAINER_FILE_NAME}"
    if [ -e "${CONTAINER_FILE_NAME}" ]; then
        cd
        echo "[INFO] fetch_and_run.sh line $LINENO: Attempting to launch container ${CONTAINER_FILE_NAME} with neurodesk_singularity_opts=${neurodesk_singularity_opts}"

        export SINGULARITYENV_PS1="${MOD_NAME}-${MOD_VERS}:\w$ "
        # shellcheck disable=SC2154
        echo "[INFO] fetch_and_run.sh line $LINENO: output README.md of the container"
        singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY ${neurodesk_singularity_opts} ${CONTAINER_FILE_NAME} cat /README.md

        singularity --silent shell ${neurodesk_singularity_opts} "${CONTAINER_FILE_NAME}"
        if [ $? -eq 0 ]; then
            echo "[INFO] fetch_and_run.sh line $LINENO: Container ran OK"
        else
            echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
            echo "[ERROR] fetch_and_run.sh line $LINENO: the container ${CONTAINER_FILE_NAME} experienced an error when starting. This could be a problem with your firewall if it uses deep packet inspection. Please ask your IT if they do this and what they are blocking. Trying a workaround next - hit Enter to try that!"
            echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"

            read -n 1 -s -r -p "Press any key to continue..."
            echo ""

            export CVMFS_DISABLE=true

            echo "[INFO] downloading the complete container as a workaround ..."

            # Resolve builddate if needed for download
            if [[ -z "$MOD_DATE" ]]; then
                MOD_DATE=$(resolve_builddate "$MOD_NAME" "$MOD_VERS")
            fi

            # shellcheck disable=SC1091
            source "${_base}"/fetch_containers.sh "$MOD_NAME" "$MOD_VERS" "$MOD_DATE"
            module use "${_base}/containers/modules"
            module load "${MOD_NAME}/${MOD_VERS}"
            CONTAINER_DIR=$(echo "$PATH" | tr ':' '\n' | head -1)
            CONTAINER_DIR_NAME=$(basename "$CONTAINER_DIR")
            CONTAINER_FILE_NAME="${CONTAINER_DIR}/${CONTAINER_DIR_NAME}.simg"
            singularity --silent exec ${neurodesk_singularity_opts} ${CONTAINER_FILE_NAME} cat /README.md
            singularity --silent shell ${neurodesk_singularity_opts} ${CONTAINER_FILE_NAME}
            if [ $? -eq 0 ]; then
                echo "[INFO] fetch_and_run.sh line $LINENO: Container ran OK"
            else
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                echo "[ERROR] fetch_and_run.sh line $LINENO: the container ${CONTAINER_FILE_NAME} doesn't exist. There is something wrong with the container download. Please ask for help here with the output of this window: https://github.com/orgs/NeuroDesk/discussions "
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                read -n 1 -s -r -p "Press any key to continue..."
            fi
        fi
    else
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        echo "[ERROR] fetch_and_run.sh line $LINENO: the container ${CONTAINER_FILE_NAME} doesn't exist. There is something wrong with the container download or CVMFS. Please ask for help here with the output of this window: https://github.com/orgs/NeuroDesk/discussions "
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        read -n 1 -s -r -p "Press any key to continue..."
    fi
else
    # Additional command provided -> Run it via the module environment
    echo "[INFO] fetch_and_run.sh line $LINENO: Running command '${@}'."
    ${@}
fi
