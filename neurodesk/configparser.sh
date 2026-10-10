#!/bin/bash

if [ $# -eq 0 ]; then
    _script="$(readlink -f "${BASH_SOURCE[0]}")" ## who am i? ##
    _base="$(dirname "$_script")" ## Delete last component from $_script ##
    filename="${_base}/config.ini"
else
    filename=$1
fi

if [ -f "$filename" ]; then
    echo "Reading config from $filename"
    while IFS='= ' read -r key value
    do
        if [[ $key == \[*] ]]
        then
            section=${key#*[}
            section=${section%]*}
        elif [[ $value ]]
        then
            declare "neurodesk_${key}=$value"
        fi
    done < "$filename"

    # This sourced configuration value is consumed by the calling script.
    # Configuration keys are assigned dynamically by sourced configparser.sh.
    # shellcheck disable=SC2034,SC2154
    neurodesk_appmenudir="$(dirname "${neurodesk_appmenu}")"
    # This sourced configuration value is consumed by the calling script.
    # shellcheck disable=SC2034
    neurodesk_appmenufile="$(basename "${neurodesk_appmenu}")"
fi
