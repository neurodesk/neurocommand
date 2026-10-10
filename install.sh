#!/bin/bash

_script="$(readlink -f "${BASH_SOURCE[0]}")" ## who am i? ##
_base="$(dirname "$_script")" ## Delete last component from $_script ##
source neurodesk/configparser.sh "${_base}"/config.ini

echo "WARNING: Will modify/replace system files!!!"
# read -p "Press enter to continue ..."

# Configuration keys are assigned dynamically by sourced configparser.sh.
# shellcheck disable=SC2154
if [ "${neurodesk_edit}" == "y" ]; then
    mv -vn "${neurodesk_appmenu}" "${neurodesk_appmenu}".BAK
    # Configuration keys are assigned dynamically by sourced configparser.sh.
    # shellcheck disable=SC2154
    ln -sfn "${neurodesk_installdir}"/"${neurodesk_appmenufile}" "${neurodesk_appmenudir}"
else
    echo "!!! Add <MergeFile>neurodesk-applications.menu</MergeFile> to ${neurodesk_appmenu} !!!"
fi
ln -sfn "${neurodesk_installdir}"/neurodesk-applications.menu "${neurodesk_appmenudir}"

# Configuration keys are assigned dynamically by sourced configparser.sh.
# shellcheck disable=SC2154
ln -sfn "${neurodesk_installdir}"/applications "${neurodesk_appdir}"/neurodesk
# Configuration keys are assigned dynamically by sourced configparser.sh.
# shellcheck disable=SC2154
ln -sfn "${neurodesk_installdir}"/desktop-directories "${neurodesk_deskdir}"/neurodesk
