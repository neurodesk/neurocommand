#!/bin/bash -i

# fetch_containers.sh [name] [version] [date]
# Example - downloads the container:
#   cd neurodesk
#   bash fetch_and_run.sh itksnap 3.8.0 20201208

# Read arguments
MOD_NAME=$1
MOD_VERS=$2
MOD_DATE=$3

IMG_NAME=${MOD_NAME}_${MOD_VERS}_${MOD_DATE}
echo "[INFO] fetch_containers.sh: IMG_NAME=$IMG_NAME"
echo "[INFO] fetch_containers.sh: SINGULARITY_BINDPATH : $SINGULARITY_BINDPATH"


_script="$(readlink -f ${BASH_SOURCE[0]})" ## who am i? ##
_base="$(dirname $_script)" ## Delete last component from $_script ##
source ${_base}/configparser.sh ${_base}/config.ini

# if $neurodesk_installdir is empty then this it's not installed and running in developer mode:
if [ -z "$neurodesk_installdir" ]; then
    echo "[WARNING] fetch_containers.sh: neurodesk_installdir is not set. Trying to set it"
    neurodesk_installdir=${_base}
    echo "[INFO] fetch_containers.sh: neurodesk_installdir=${neurodesk_installdir}"
fi

# default path is in the home directory of the user executing the call - except if there is a system wide install:
export PATH_PREFIX=${neurodesk_installdir}
export CONTAINER_PATH=${NEURODESKTOP_LOCAL_CONTAINERS:-${PATH_PREFIX}/containers}
export MODS_PATH=${CONTAINER_PATH}/modules

echo "[INFO] fetch_containers.sh: CONTAINER_PATH=$CONTAINER_PATH"
echo "[INFO] fetch_containers.sh: MODS_PATH=$MODS_PATH"

echo "[INFO] fetch_containers.sh: trying to module use  ${MODS_PATH}"
if [ -f '/usr/share/module.sh' ]; then source /usr/share/module.sh; fi
module use ${MODS_PATH}

if [ ! -L `readlink -f $CONTAINER_PATH` ]; then
    echo "[INFO] fetch_containers.sh: creating `readlink -f $CONTAINER_PATH`"
    mkdir -p `readlink -f $CONTAINER_PATH` || ( echo "Something went wrong. " && exit )
fi

if [ ! -d `readlink -f $MODS_PATH` ]; then
    echo "[INFO] fetch_containers.sh: creating `readlink -f $MODS_PATH`"
    mkdir -p `readlink -f $MODS_PATH` || ( echo "Something went wrong. " && exit )
fi
# Update application transparent-singularity with latest version
cd ${CONTAINER_PATH}
mkdir -p ${IMG_NAME}

echo "[CHECK] fetch_containers.sh: Check if the container is there - if not this means we definitely need to install the container"


CONTAINER_FILE_NAME=${CONTAINER_PATH}/${IMG_NAME}/${IMG_NAME}.simg
if [ -e "${CONTAINER_FILE_NAME}" ]; then
    echo "[INFO] fetch_containers.sh: found it. Container ${IMG_NAME} is there."
    echo "[INFO] fetch_containers.sh: now checking if container is fully downloaded and executable:"
    qq=`which  singularity`
    if [[  ${#qq} -lt 1 ]]; then
        echo "[ERROR] fetch_containers.sh: This script requires singularity/apptainer on your path. EXITING"
        read -n 1 -s -r -p "Press any key to exit..."
        exit 2
    fi

    echo "[INFO] fetch_containers.sh: copying transparent singularity files from ${neurodesk_installdir} to ${CONTAINER_PATH}/${IMG_NAME} ..."
    cp -u ${neurodesk_installdir}/transparent-singularity/*.sh ${CONTAINER_PATH}/${IMG_NAME}/
    cp -u ${neurodesk_installdir}/transparent-singularity/ts_* ${CONTAINER_PATH}/${IMG_NAME}/

    echo "[INFO] fetch_containers.sh: testing if the container runs:"
    singularity exec ${neurodesk_singularity_opts} ${CONTAINER_FILE_NAME} ls
    if [ $? -ne 0 ]; then
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        echo "the container is incomplete and needs to be re-downloaded. You could try:"
        echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
        echo "rm -rf ${CONTAINER_PATH}/${MOD_NAME}_${MOD_VERS}_*" 
        echo "rm -rf ${MODS_PATH}/${MOD_NAME}/${MOD_VERS}"
        read -n 1 -s -r -p "Press any key to exit..."
        exit 2
    else 
        echo "[INFO] fetch_containers.sh: Container ${IMG_NAME} seems to be fully downloaded and executable."        
    fi
else
    echo "[INFO] fetch_containers.sh: copying transparent singularity files from ${neurodesk_installdir} to ${CONTAINER_PATH}/${IMG_NAME} ..."
    cp ${neurodesk_installdir}/transparent-singularity/*.sh ${CONTAINER_PATH}/${IMG_NAME}/
    cp ${neurodesk_installdir}/transparent-singularity/ts_* ${CONTAINER_PATH}/${IMG_NAME}/
    echo "[INFO] fetch_containers.sh: changing directory to: ${CONTAINER_PATH}/${IMG_NAME}"
    cd ${CONTAINER_PATH}/${IMG_NAME}
    echo "[INFO] fetch_containers.sh: executing run_transparent_singularity.sh --container ${IMG_NAME}.simg in $PWD"
   ${CONTAINER_PATH}/${IMG_NAME}/run_transparent_singularity.sh --container ${IMG_NAME}.simg --singularity-opts "${neurodesk_singularity_opts}"
    # rm -rf .git* README.md run_transparent_singularity ts_*
fi

