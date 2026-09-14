#!/usr/bin/env bash

# Render searchable whatis metadata for the commands exposed by a container.

commands_file="${1:-}"

if [[ -z "$commands_file" ]]; then
    echo "Usage: ts_command_metadata.sh COMMANDS_FILE" >&2
    exit 2
fi

if [[ ! -f "$commands_file" ]]; then
    echo "[ERROR] ts_command_metadata.sh: Commands file not found: $commands_file" >&2
    exit 2
fi

command_list=""
while IFS= read -r command; do
    command="${command%$'\r'}"
    [[ -n "$command" ]] || continue

    # Executable permission also admits libraries and hidden installer hooks.
    # Keep this filter in sync with cvmfs/reconcile_module_files.py.
    case "${command,,}" in
        .*|*.so|*.so.*|*.dylib|*.dll) continue ;;
    esac

    if [[ "$command" == *"/"* || "$command" =~ [[:space:]] ]]; then
        echo "[WARN] ts_command_metadata.sh: Skipping invalid command name: $command" >&2
        continue
    fi

    if [[ -n "$command_list" ]]; then
        command_list+=", "
    fi
    command_list+="${command}"
done < <(LC_ALL=C sort -u "$commands_file")

if [[ -z "$command_list" ]]; then
    exit 0
fi

# Escape the two characters that are special inside a Lua double-quoted string.
command_list="${command_list//\\/\\\\}"
command_list="${command_list//\"/\\\"}"

echo "-- neurodesk-exposed-commands"
printf 'whatis("Commands: %s")\n' "$command_list"
