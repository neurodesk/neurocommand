#!/bin/bash

if [[ -z "$1" ]] || [ "$1" = "-h" ] || [ "$1" = "--help" ] ; then
    echo "Shows or installs available Neurodesk containers"
    echo
    echo "Usage:"
    echo "  $0 all         # Shows all available Neurodesk containers"
    echo "  $0 --all       # Installs all available Neurodesk containers"
    echo "  $0 PATTERN     # Shows all containers that match PATTERN"
    echo "  $0 --PATTERN   # Installs all containers that match PATTERN"
    echo
    echo "Examples:"
    echo "  $0 all"
    echo "  $0 diffusion"
    echo "  $0 --bidscoin"
    echo
    exit 1
fi

_script="$(readlink -f "${BASH_SOURCE[0]}")" ## who am i? ##
_base="$(dirname "$_script")" ## Delete last component from $_script ##
source neurodesk/configparser.sh "${_base}"/config.ini

install="false"
pattern=$1
if [ "${1:0:2}" = '--' ]; then
    install="true"
    pattern=${1:2}
fi

echo "--------------------------------------"
if [ "$install" = "true" ]; then
    echo "Installing *${pattern}* containers"
else
    echo "To install ALL containers, run:"
    echo "$0 --all"
    echo "--------------------------------------"
    echo "To install individual containers, run:"
fi
echo "--------------------------------------"
echo

catalog_names=$(python3 -c '
import json, sys
with open(sys.argv[1]) as catalog:
    print("\n".join(sorted(json.load(catalog), key=len, reverse=True)))
' "${_base}/neurodesk/apps.json") || exit $?
mapfile -t tool_names <<< "$catalog_names"

while read -r identity categories; do

    builddate=${identity##*_}
    name_version=${identity%_*}
    name=${name_version%%_*}
    for tool_name in "${tool_names[@]}"; do
        if [[ $name_version == "${tool_name}_"* ]]; then
            name=$tool_name
            break
        fi
    done
    version=${name_version#"${name}_"}
    if [ "$pattern" != "all" ] && [[ $name != *${pattern}* ]]; then
        continue
    fi
    categories=${categories#categories:}
    categories=${categories%,}
    apphead="| ${name} | ${version} | ${builddate} | ${categories} | Run:"
    # Configuration keys are assigned dynamically by sourced configparser.sh.
    # shellcheck disable=SC2154
    appfetch=("${neurodesk_installdir}/fetch_containers.sh" "$name" "$version" "$builddate")

    printf '%*s' "${#apphead}" '' | tr ' ' '-'
    echo
    echo "$apphead"
    printf '%*s' "${#apphead}" '' | tr ' ' '-'
    echo
    printf '%q' "${appfetch[0]}"
    printf ' %q' "${appfetch[@]:1}"
    echo
    echo

    if [ "$install" = "true" ]; then
        "${appfetch[@]}"
        err=$?
        if [ $err -eq 0 ] ; then
            installmsg="| SUCCESS | ${name} ${version} ${builddate} | $(date)"
            printf '%*s' "${#installmsg}" '' | tr ' ' '-'
            echo
            echo "$installmsg"
            printf '%*s' "${#installmsg}" '' | tr ' ' '-'
            echo
            echo
            echo
        else
            installmsg="| FAILED | ${name} ${version} ${builddate} | $(date)"
            printf '%*s' "${#installmsg}" '' | tr ' ' '-'
            echo
            echo "$installmsg"
            printf '%*s' "${#installmsg}" '' | tr ' ' '-'
            echo
            echo
            echo "Existing due to install error(s) ..."
            echo
            exit
        fi
    fi

done < cvmfs/log.txt
