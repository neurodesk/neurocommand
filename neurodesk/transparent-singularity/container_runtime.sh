#!/usr/bin/env bash

neurodesk_runtime() {
    local runtime=${NEURODESK_CONTAINER_RUNTIME:-} level=${NEURODESK_CONTAINER_LOG_LEVEL:-quiet}
    local flags=()
    if [[ ${NEURODESK_CONTAINER_RUNTIME+x} ]]; then
        command -v "$runtime" >/dev/null 2>&1 || { echo "[ERROR] Requested runtime is unavailable: $runtime" >&2; return 2; }
    elif command -v apptainer >/dev/null 2>&1; then
        runtime=apptainer
    elif command -v singularity >/dev/null 2>&1; then
        runtime=singularity
    else
        echo '[ERROR] Install apptainer or singularity, or set NEURODESK_CONTAINER_RUNTIME.' >&2
        return 2
    fi
    case $level in
        quiet) flags=(--silent);; normal) ;; debug) flags=(--debug);;
        *) echo "[ERROR] Invalid NEURODESK_CONTAINER_LOG_LEVEL: $level" >&2; return 2;;
    esac
    "$runtime" "${flags[@]}" "$@"
}

neurodesk_container() (
    local operation=$1 image=$2 gpu=${NEURODESK_GPU:-auto} customtmp option
    shift 2
    [[ -e $image ]] || { echo "[ERROR] Missing container image: $image" >&2; return 2; }
    PWD="$(pwd -P)"
    export PWD
    local options=() legacy=() gpu_options=()
    read -r -a legacy <<< "${neurodesk_singularity_opts:-}"
    case $gpu in
        auto)
            if [ -f /proc/driver/nvidia/version ] && [ -z "${APPTAINER_NV+set}" ] && [ -z "${SINGULARITY_NV+set}" ]; then
                export APPTAINER_NV=1 SINGULARITY_NV=1
            fi;;
        on) export APPTAINER_NV=1 SINGULARITY_NV=1; gpu_options=(--nv);;
        off)
            unset APPTAINER_NV SINGULARITY_NV
            gpu_options=(--no-nv)
            local filtered=()
            for option in "${legacy[@]}"; do [[ $option == --nv ]] || filtered+=("$option"); done
            legacy=("${filtered[@]}");;
        *) echo "[ERROR] Invalid NEURODESK_GPU: $gpu" >&2; return 2;;
    esac
    if [[ $operation == exec ]]; then options+=(--cleanenv); fi
    if [[ ${NEURODESK_CONTAINER_LEGACY_ENV:-0} != 1 ]]; then options+=(--env "DISPLAY=${DISPLAY:-}"); fi
    if [[ -n ${XAUTHORITY:-} && -f $XAUTHORITY ]]; then
        options+=(--bind "$XAUTHORITY:$XAUTHORITY:ro")
        if [[ ${NEURODESK_CONTAINER_LEGACY_ENV:-0} != 1 ]]; then options+=(--env "XAUTHORITY=$XAUTHORITY"); fi
    fi
    local tmp=''
    for customtmp in TMP TMPDIR TEMP TEMPDIR; do [[ -z ${!customtmp:-} ]] || tmp=${!customtmp}; done
    [[ -z $tmp ]] || options+=(--bind "$tmp:/tmp")
    neurodesk_runtime "$operation" "${options[@]}" "${legacy[@]}" "${gpu_options[@]}" --pwd "$PWD" "$image" "$@"
)

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then neurodesk_runtime "$@"; fi
