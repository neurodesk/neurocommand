#!/bin/bash -i

# fetch_containers.sh [name] [version] [date]
# Example - downloads the container:
#   cd neurodesk
#   bash fetch_and_run.sh itksnap 3.8.0 20201208

# Read arguments
MOD_NAME=$1
MOD_VERS=$2
MOD_DATE=$3

if [[ -z "$MOD_NAME" ]] || [[ -z "$MOD_VERS" ]] || [[ ! "$MOD_DATE" =~ ^[0-9]{8}$ ]]; then
    echo "[ERROR] fetch_containers.sh: Usage: fetch_containers.sh [name] [version] [YYYYMMDD build date]" >&2
    echo "[ERROR] fetch_containers.sh: Refusing to create a container path with invalid build date '${MOD_DATE:-<empty>}'." >&2
    exit 2
fi

IMG_NAME=${MOD_NAME}_${MOD_VERS}_${MOD_DATE}
echo "[INFO] fetch_containers.sh: IMG_NAME=$IMG_NAME"
echo "[INFO] fetch_containers.sh: SINGULARITY_BINDPATH : $SINGULARITY_BINDPATH"


_script="$(readlink -f "${BASH_SOURCE[0]}")"
_base="$(dirname "$_script")"
source "${_base}/configparser.sh" "${_base}/config.ini"

# if $neurodesk_installdir is empty then this it's not installed and running in developer mode:
if [ -z "$neurodesk_installdir" ]; then
    echo "[WARNING] fetch_containers.sh: neurodesk_installdir is not set. Trying to set it"
    neurodesk_installdir=${_base}
    echo "[INFO] fetch_containers.sh: neurodesk_installdir=${neurodesk_installdir}"
fi

# default path is in the home directory of the user executing the call - except if there is a system wide install:
export PATH_PREFIX=${neurodesk_installdir}
if [[ -d "$_base/containers/$IMG_NAME" ]]; then
    PATH_PREFIX=$_base
fi
export CONTAINER_PATH=${NEURODESKTOP_LOCAL_CONTAINERS:-${PATH_PREFIX}/containers}
export MODS_PATH=${CONTAINER_PATH}/modules

echo "[INFO] fetch_containers.sh: CONTAINER_PATH=$CONTAINER_PATH"
echo "[INFO] fetch_containers.sh: MODS_PATH=$MODS_PATH"

refresh=false
if [[ ${4:-} == --refresh ]]; then
    refresh=true
elif [[ ${4:-} == -* ]]; then
    echo "[ERROR] fetch_containers.sh: Unknown option '${4}'." >&2
    exit 2
fi

CONTAINER_DIR="${CONTAINER_PATH}/${IMG_NAME}"
CONTAINER_FILE_NAME="${CONTAINER_DIR}/${IMG_NAME}.simg"
has_inventories=false
if [[ -s "$CONTAINER_DIR/commands.txt" && -f "$CONTAINER_DIR/env.txt" ]]; then
    has_inventories=true
fi

if [[ "$refresh" == true ]]; then
    if [[ ! -e "$CONTAINER_FILE_NAME" || "$has_inventories" != true ]]; then
        echo "[ERROR] fetch_containers.sh: Refresh requires the installed image, commands.txt and env.txt in $CONTAINER_DIR. Install normally first." >&2
        exit 2
    fi
elif [[ -e "$CONTAINER_FILE_NAME" ]]; then
    echo "[INFO] fetch_containers.sh: Container ${IMG_NAME} is there. Checking that it is fully downloaded and executable:"
    if ! command -v singularity >/dev/null 2>&1; then
        echo "[ERROR] fetch_containers.sh: This script requires singularity/apptainer on your path. EXITING" >&2
        read -n 1 -s -r -p "Press any key to exit..."
        exit 2
    fi
    if ! singularity exec ${neurodesk_singularity_opts} "${CONTAINER_FILE_NAME}" ls; then
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        echo "the container is incomplete and needs to be re-downloaded. You could try:"
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        echo "rm -rf ${CONTAINER_PATH}/${MOD_NAME}_${MOD_VERS}_*"
        echo "rm -rf ${MODS_PATH}/${MOD_NAME}/${MOD_VERS} ${MODS_PATH}/${MOD_NAME}/${MOD_VERS}.lua"
        read -n 1 -s -r -p "Press any key to exit..."
        exit 2
    fi

    container_dir=$(readlink -f "$CONTAINER_DIR")
    if grep -Fqx "prepend_path(\"PATH\", \"${container_dir}\")" "${MODS_PATH}/${MOD_NAME}/${MOD_VERS}.lua" 2>/dev/null \
        && grep -Fqx "prepend-path PATH \"${container_dir}\"" "${MODS_PATH}/${MOD_NAME}/${MOD_VERS}" 2>/dev/null; then
        echo "[INFO] fetch_containers.sh: Wrappers and modulefiles for ${IMG_NAME} are up to date."
        exit 0
    fi
    # Saved inventories let a moved installation be regenerated without executing the image again.
    refresh=$has_inventories
fi

if [[ "$refresh" != true ]]; then
    if ! type module >/dev/null 2>&1 && [[ -f /usr/share/module.sh ]]; then
        source /usr/share/module.sh
    fi
    if type module >/dev/null 2>&1; then
        module use "$MODS_PATH" || exit 2
    fi
    mkdir -p "$CONTAINER_DIR" "$MODS_PATH" || exit 2
fi

helper_dir="${_base}/transparent-singularity"
cp "$helper_dir"/*.sh "$CONTAINER_DIR/" || exit 2
cp "$helper_dir"/ts_* "$CONTAINER_DIR/" || exit 2
mkdir -p "$CONTAINER_DIR/manual_module_files" || exit 2
cp -R "$helper_dir/manual_module_files/." "$CONTAINER_DIR/manual_module_files/" || exit 2

if [[ "$refresh" == true ]]; then
    echo "[INFO] fetch_containers.sh: Regenerating wrappers and modulefiles for ${IMG_NAME} from saved inventories."
    bash "$CONTAINER_DIR/run_transparent_singularity.sh" --container "$IMG_NAME.simg" --refresh || exit 2
else
    bash "$CONTAINER_DIR/run_transparent_singularity.sh" --container "$IMG_NAME.simg" --singularity-opts "${neurodesk_singularity_opts}" || exit 2
fi
