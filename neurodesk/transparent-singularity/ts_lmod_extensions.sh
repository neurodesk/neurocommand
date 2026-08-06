#!/usr/bin/env bash

# Render Lmod extension metadata for the commands exposed by a container.
# Lmod extensions must use the form name/version, with comma-separated entries.

commands_file="${1:-}"
module_version="${2:-}"

if [[ -z "$commands_file" || -z "$module_version" ]]; then
    echo "Usage: ts_lmod_extensions.sh COMMANDS_FILE MODULE_VERSION" >&2
    exit 2
fi

if [[ ! -f "$commands_file" ]]; then
    echo "[ERROR] ts_lmod_extensions.sh: Commands file not found: $commands_file" >&2
    exit 2
fi

if [[ "$module_version" == *","* || "$module_version" == *"/"* || "$module_version" =~ [[:space:]] ]]; then
    echo "[ERROR] ts_lmod_extensions.sh: Invalid Lmod extension version: $module_version" >&2
    exit 2
fi

extension_list=""
while IFS= read -r command; do
    command="${command%$'\r'}"
    [[ -n "$command" ]] || continue

    if [[ "$command" == *","* || "$command" == *"/"* || "$command" =~ [[:space:]] ]]; then
        echo "[WARN] ts_lmod_extensions.sh: Skipping command that cannot be represented as an Lmod extension: $command" >&2
        continue
    fi

    if [[ -n "$extension_list" ]]; then
        extension_list+=", "
    fi
    extension_list+="${command}/${module_version}"
done < <(LC_ALL=C sort -u "$commands_file")

if [[ -z "$extension_list" ]]; then
    exit 0
fi

# Escape the two characters that are special inside a Lua double-quoted string.
extension_list="${extension_list//\\/\\\\}"
extension_list="${extension_list//\"/\\\"}"

echo "-- neurodesk-exposed-commands"
printf 'extensions("%s")\n' "$extension_list"
