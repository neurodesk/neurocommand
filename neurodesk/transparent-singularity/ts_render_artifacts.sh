#!/usr/bin/env bash

set -e
base=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
container=$1
stem=${container%.simg}
name_version=${stem%_*}
version=${name_version##*_}
name=${name_version%_*}

[[ -e "$base/$container" ]] || { echo "[ERROR] Missing image: $base/$container" >&2; exit 2; }
[[ -s "$base/commands.txt" ]] || { echo "[ERROR] Missing or empty commands.txt. Install the container normally to save its inventories before refresh." >&2; exit 2; }
[[ -f "$base/env.txt" ]] || { echo "[ERROR] Missing env.txt. Install the container normally to save its inventories before refresh." >&2; exit 2; }

lua_quote() {
    local value=$1
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    printf '"%s"' "$value"
}

tcl_quote() {
    local value=$1
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//\$/\\\$}
    value=${value//\[/\\[}
    value=${value//\]/\\]}
    value=${value//\{/\\{}
    value=${value//\}/\\\}}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    printf '"%s"' "$value"
}

shell_quote() {
    printf "'%s'" "${1//\'/\'\\\'\'}"
}

while IFS= read -r executable || [[ -n "$executable" ]]; do
    [[ -n "$executable" ]] || continue
    [[ "$executable" != */* && "$executable" != . && "$executable" != .. && ! "$executable" =~ [[:space:]] ]] || continue
    {
        cat <<'WRAPPER'
#!/usr/bin/env bash
export PWD=`pwd -P`
if [ -f /proc/driver/nvidia/version ] && [ -z "${APPTAINER_NV+set}" ] && [ -z "${SINGULARITY_NV+set}" ]; then
  export APPTAINER_NV=1
  export SINGULARITY_NV=1
fi
xauthority_opts=()
if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then
  xauthority_opts=(--bind "$XAUTHORITY:$XAUTHORITY:ro" --env "XAUTHORITY=$XAUTHORITY")
fi
tmp_opts=()
for customtmp in TMP TMPDIR TEMP TEMPDIR; do
  if [[ -n "${!customtmp}" ]]; then
    tmp_opts=(--bind "${!customtmp}:/tmp")
  fi
done
WRAPPER
        gui_opts='--env DISPLAY="$DISPLAY" "${xauthority_opts[@]}" '
        [[ ${2:-modern} != legacy ]] || gui_opts=""
        printf 'singularity --silent exec --cleanenv %s"${tmp_opts[@]}" $neurodesk_singularity_opts --pwd "$PWD" %s %s "$@"\n' "$gui_opts" "$(shell_quote "$base/$container")" "$(shell_quote "$executable")"
    } > "$base/$executable"
    chmod a+x "$base/$executable"
done < "$base/commands.txt"

{
    echo '#!/usr/bin/env bash'
    printf 'source %q\n' "$base/deactivate_${container}.sh"
    printf 'export PATH=%q:"$PATH"\n' "$base"
    printf 'printf "# Container in %%s\\n" %q >> ~/.bashrc\n' "$base"
    printf 'printf '"'"'export PATH="%%s:$PATH"\\n'"'"' %q >> ~/.bashrc\n' "$base"
} > "$base/activate_${container}.sh"
{
    printf 'pathToRemove=%q\n' "$base"
    cat "$base/ts_deactivate_"
} > "$base/deactivate_${container}.sh"
chmod a+x "$base/activate_${container}.sh" "$base/deactivate_${container}.sh"

module_path="$base/../modules/$name"
mkdir -p "$module_path"
lua_file="$module_path/$version.lua"
tcl_file="$module_path/$version"
help_text=""
[[ ! -f "$base/README.md" ]] || help_text=$(cat "$base/README.md")
{
    echo '-- -*- lua -*-'
    echo 'help([===['
    if [[ -f "$base/README.md" ]]; then
        bash "$base/ts_sanitize_lua_help.sh" "$base/README.md"
    fi
    echo ']===])'
    printf 'whatis(%s)\n' "$(lua_quote "$container")"
    bash "$base/ts_command_metadata.sh" "$base/commands.txt"
    printf 'prepend_path("PATH", %s)\n' "$(lua_quote "$base")"
} > "$lua_file"
{
    echo '#%Module1.0'
    printf 'proc ModulesHelp { } { puts stderr %s }\n' "$(tcl_quote "$help_text")"
    printf 'module-whatis %s\n' "$(tcl_quote "$container")"
    bash "$base/ts_command_metadata.sh" "$base/commands.txt" tcl
    printf 'prepend-path PATH %s\n' "$(tcl_quote "$base")"
} > "$tcl_file"

while IFS= read -r record || [[ -n "$record" ]]; do
    [[ "$record" == DEPLOY_ENV_*=* ]] || continue
    variable=${record%%=*}
    variable=${variable#DEPLOY_ENV_}
    value=${record#*=}
    value=${value//BASEPATH/$base/$container}
    printf 'setenv(%s, %s)\n' "$(lua_quote "$variable")" "$(lua_quote "$value")" >> "$lua_file"
    printf 'setenv %s %s\n' "$(tcl_quote "$variable")" "$(tcl_quote "$value")" >> "$tcl_file"
done < "$base/env.txt"

for format in lua tcl; do
    if [[ "$format" == lua ]]; then
        snippet="$base/manual_module_files/$name"
        target=$lua_file
        marker='--'
    else
        snippet="$base/manual_module_files/tcl/$name"
        target=$tcl_file
        marker='#'
    fi
    if [[ -f "$snippet" ]]; then
        {
            printf '%s neurodesk-manual-module-begin\n' "$marker"
            printf '%s\n' "$(sed "s/toolVersion/$version/g" "$snippet")"
            printf '%s neurodesk-manual-module-end\n' "$marker"
        } >> "$target"
    fi
done

uninstall_tmp=$(mktemp "$base/.ts_uninstall.XXXXXX")
sed '/^# neurodesk-module-uninstall-begin$/,/^# neurodesk-module-uninstall-end$/d' "$base/ts_uninstall.sh" > "$uninstall_tmp"
{
    echo '# neurodesk-module-uninstall-begin'
    printf 'rm -f %q %q\n' "$lua_file" "$tcl_file"
    echo '# neurodesk-module-uninstall-end'
} >> "$uninstall_tmp"
chmod a+rx "$uninstall_tmp"
mv "$uninstall_tmp" "$base/ts_uninstall.sh"
