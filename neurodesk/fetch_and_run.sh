#!/usr/bin/env bash
# fetch_and_run.sh NAME VERSION [BUILD_DATE] [--container-shell | COMMAND ARGS...]

_neurodesk_base=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# This source is deployed beside the script or installed by the module engine.
# shellcheck disable=SC1091
source "$_neurodesk_base/configparser.sh" "$_neurodesk_base/config.ini" >/dev/null || exit 2
export neurodesk_singularity_opts

neurodesk_init_modules() {
    if type module >/dev/null 2>&1; then
        if [[ $(declare -f module) != *'_module_raw'* ]] || type _module_raw >/dev/null 2>&1; then return 0; fi
        # Modules can export its public function without its internal helper.
        if [[ -n ${MODULES_CMD:-} ]]; then
            if [[ $MODULES_CMD == *.tcl ]]; then eval "$(tclsh "$MODULES_CMD" bash autoinit)";
            else eval "$("$MODULES_CMD" bash autoinit)"; fi
            return $?
        fi
    fi
    if [[ -n ${LMOD_INIT:-} && -f $LMOD_INIT ]]; then
        # The initialization file is supplied by the deployment environment.
        # shellcheck disable=SC1090
        source "$LMOD_INIT"
    elif [[ -n ${MODULES_CMD:-} ]]; then
        if [[ $MODULES_CMD == *.tcl ]]; then
            eval "$(tclsh "$MODULES_CMD" bash autoinit)"
        else
            eval "$("$MODULES_CMD" bash autoinit)"
        fi
    elif [[ -n ${LMOD_CMD:-} && -f $(dirname "$LMOD_CMD")/../init/bash ]]; then
        # This source is deployed beside the script or installed by the module engine.
        # shellcheck disable=SC1091
        source "$(dirname "$LMOD_CMD")/../init/bash"
    elif [[ -f /usr/share/module.sh ]]; then
        # This source is deployed beside the script or installed by the module engine.
        # shellcheck disable=SC1091
        source /usr/share/module.sh
    elif [[ -f /etc/profile.d/modules.sh ]]; then
        # This source is deployed beside the script or installed by the module engine.
        # shellcheck disable=SC1091
        source /etc/profile.d/modules.sh
    elif [[ -f /usr/share/lmod/lmod/init/bash ]]; then
        # This source is deployed beside the script or installed by the module engine.
        # shellcheck disable=SC1091
        source /usr/share/lmod/lmod/init/bash
    elif [[ -f /usr/share/modules/init/bash ]]; then
        # This source is deployed beside the script or installed by the module engine.
        # shellcheck disable=SC1091
        source /usr/share/modules/init/bash
    fi
    type module >/dev/null 2>&1 || { echo '[ERROR] Initialize Lmod or Environment Modules before launching Neurodesk.' >&2; return 2; }
}

name=${1:-}
version=${2:-}
if [[ ! $name =~ ^[A-Za-z0-9][A-Za-z0-9_.+-]*$ || ! $version =~ ^[A-Za-z0-9][A-Za-z0-9_.+-]*$ ]]; then
    echo 'Usage: fetch_and_run.sh NAME VERSION [BUILD_DATE] [--container-shell | COMMAND ARGS...]' >&2
    exit 2
fi
shift 2
builddate=''
if [[ ${1:-} =~ ^[0-9]{8}$ ]]; then builddate=$1; shift; fi
shell_mode=${NEURODESK_SHELL_MODE:-host}
case $shell_mode in host|container) ;; *) echo "[ERROR] Invalid NEURODESK_SHELL_MODE: $shell_mode" >&2; exit 2;; esac
isolated=false
if [[ ${1:-} == --container-shell ]]; then isolated=true; shift
elif [[ ${1:-} == --host-shell ]]; then shift
elif [[ $# -eq 0 && $shell_mode == container ]]; then isolated=true; fi
if [[ $isolated == true && $# -gt 0 ]]; then echo '[ERROR] --container-shell does not accept a named command.' >&2; exit 2; fi

resolved=$(python3 "$_neurodesk_base/transparent-singularity/artifact_renderer.py" \
    --manifest "$_neurodesk_base/bundles.json" --catalog "$_neurodesk_base/apps.json" --resolve "$name" "$version") || exit 2
mapfile -t records <<< "$resolved"
kind=${records[0]}
if [[ $kind == bundle && ( -n $builddate || $isolated == true ) ]]; then
    echo '[ERROR] A bundle has no container image or build date. Launch its host shell or a named executable.' >&2
    exit 2
fi

local_root=${NEURODESKTOP_LOCAL_CONTAINERS:-$_neurodesk_base/containers}
cvmfs_root=${NEURODESK_CVMFS_ROOT:-/cvmfs/neurodesk.ardc.edu.au}
mods_path=$local_root/modules
if [[ ${CVMFS_DISABLE:-false} == false && -d $cvmfs_root/containers/modules ]]; then
    mods_path+=:$cvmfs_root/containers/modules
fi
neurodesk_init_modules || exit 2
MODULES_PAGER=cat LMOD_PAGER=cat PAGER=cat module use "$mods_path" || exit 2
avail_flags=()
module_function=$(declare -f module)
if [[ $module_function != *_module_raw* && $module_function != *modulecmd.tcl* && -n ${LMOD_VERSION:-} ]]; then avail_flags=(--ignore-cache); fi

module_is_available() {
    MODULES_PAGER=cat LMOD_PAGER=cat PAGER=cat module "${avail_flags[@]}" avail "$1" 2>&1 | python3 -c 'import re,sys; sys.exit(0 if re.search(r"(?:^|\s)" + re.escape(sys.argv[1]) + r"(?=$|\s|\()", sys.stdin.read()) else 1)' "$1"
}

for record in "${records[@]:1}"; do
    IFS=$'\t' read -r dep_name dep_version dep_date <<< "$record"
    if [[ -n $builddate ]]; then dep_date=$builddate; fi
    if [[ -n $builddate ]] || ! module_is_available "$dep_name/$dep_version"; then
        if [[ ! $dep_date =~ ^[0-9]{8}$ ]]; then
            echo "[ERROR] No catalog build date for missing module $dep_name/$dep_version." >&2
            exit 2
        fi
        bash "$_neurodesk_base/fetch_containers.sh" "$dep_name" "$dep_version" "$dep_date" || exit 2
        MODULES_PAGER=cat LMOD_PAGER=cat PAGER=cat module use "$mods_path" || exit 2
    fi
done

# Installing a missing dependency also makes the declared bundle available.
if [[ $kind == bundle ]] && ! module_is_available "$name/$version"; then
    python3 "$_neurodesk_base/transparent-singularity/artifact_renderer.py" \
        --manifest "$_neurodesk_base/bundles.json" --catalog "$_neurodesk_base/apps.json" --module-root "$local_root/modules" || exit 2
fi
MODULES_PAGER=cat LMOD_PAGER=cat PAGER=cat module load "$name/$version" || exit 2
if [[ ! ${SINGULARITY_BINDPATH+x} && ! ${APPTAINER_BINDPATH+x} ]]; then
    export SINGULARITY_BINDPATH=$PWD
    if [[ -d $cvmfs_root ]]; then export SINGULARITY_BINDPATH+=,$cvmfs_root; fi
fi

if [[ $kind == container ]]; then
    key=$(python3 - "$name" "$version" <<'PY'
import sys
print('NEURODESK_IMAGE_' + '/'.join(sys.argv[1:]).encode().hex().upper())
PY
)
    image=${!key:-}
    if [[ ( $isolated == true || -n $builddate || -v $key ) && ( -z $image || ! -e $image ) ]]; then
        echo "[ERROR] Module $name/$version has no valid image identity. Refresh its generated modulefile." >&2
        exit 2
    fi
    if [[ -n $builddate && $(basename "$image") != "${name}_${version}_${builddate}.simg" ]]; then
        echo "[ERROR] Explicit builddate $builddate requested; module resolved to $image." >&2
        exit 2
    fi
fi

if [[ $isolated == true ]]; then
    # This source is deployed beside the script or installed by the module engine.
    # shellcheck disable=SC1091
    source "$_neurodesk_base/transparent-singularity/container_runtime.sh" || exit 2
    export APPTAINERENV_PS1="[$name/$version] \\w\\$ "
    export SINGULARITYENV_PS1=$APPTAINERENV_PS1
    neurodesk_container shell "$image"
elif [[ $# -gt 0 ]]; then
    "$@"
else
    ordered_keys=()
    IFS=: read -r -a current_path <<< "$PATH"
    for entry in "${current_path[@]}"; do
        for key in ${!NEURODESK_IMAGE_@}; do
            [[ $key =~ ^NEURODESK_IMAGE_[A-F0-9]+$ ]] || continue
            value=${!key}
            if [[ ${value%/*} == "$entry" ]]; then ordered_keys+=("$key"); break; fi
        done
    done
    rcfile=$(mktemp "${TMPDIR:-/tmp}/neurodesk-shell.XXXXXX") || exit 2
    trap 'rm -f -- "$rcfile"' EXIT
    {
        if [[ -f ${HOME:-}/.bashrc ]]; then printf 'source %q\n' "$HOME/.bashrc"; fi
        declare -f neurodesk_init_modules
        printf 'neurodesk_init_modules || return\n'
        printf 'MODULES_PAGER=cat LMOD_PAGER=cat PAGER=cat module use %q || return\n' "$mods_path"
        printf 'MODULES_PAGER=cat LMOD_PAGER=cat PAGER=cat module load %q || return\n' "$name/$version"
        printf '_neurodesk_keys=('
        if [[ ${#ordered_keys[@]} -gt 0 ]]; then printf '%q ' "${ordered_keys[@]}"; fi
        printf ')\n'
        cat <<'RC'
# Startup may reset PATH while native module ownership remains loaded.
_neurodesk_dirs=()
for _neurodesk_key in "${_neurodesk_keys[@]}"; do
    _neurodesk_image=${!_neurodesk_key:-}
    [[ -z $_neurodesk_image ]] || _neurodesk_dirs+=("${_neurodesk_image%/*}")
done
_neurodesk_remaining=()
IFS=: read -r -a _neurodesk_parts <<< "$PATH"
for _neurodesk_part in "${_neurodesk_parts[@]}"; do
    _neurodesk_owned=false
    for _neurodesk_dir in "${_neurodesk_dirs[@]}"; do
        if [[ $_neurodesk_dir == "$_neurodesk_part" ]]; then _neurodesk_owned=true; break; fi
    done
    [[ $_neurodesk_owned == true ]] || _neurodesk_remaining+=("$_neurodesk_part")
done
PATH=$(IFS=:; echo "${_neurodesk_dirs[*]}${_neurodesk_dirs:+:}${_neurodesk_remaining[*]}")
export PATH
unset _neurodesk_keys _neurodesk_dirs _neurodesk_key _neurodesk_image _neurodesk_remaining _neurodesk_parts _neurodesk_part _neurodesk_owned _neurodesk_dir
RC
        printf 'if ! type ml >/dev/null 2>&1; then ml() { module "$@"; }; fi\n'
        # Emit this expression literally into the child shell initialization file.
        # shellcheck disable=SC2016
        printf 'PS1=%q"${PS1:-\\w\\$ }"\n' "[$name/$version] "
    } > "$rcfile"
    bash --noprofile --rcfile "$rcfile" -i
fi
