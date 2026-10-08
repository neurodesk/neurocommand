#!/usr/bin/env bash
# Replace a container command wrapper with a notice that exits non-zero.
#
# Usage:
#   write_disabled_wrapper.sh <wrapper-path> <docker-image-ref>   write the wrapper
#   write_disabled_wrapper.sh --check <wrapper-path>               exit 0 if already current
#
# A disabled command must fail: scripts, notebooks and pipelines that call it
# (often with set -e or check=True) would otherwise treat it as successful and
# fail much later with a misleading error.
set -euo pipefail

NOTICE="This container was disabled due to a known bug or vulnerability."
EXIT_LINE="exit 1"

if [[ "${1:-}" == "--check" ]]; then
    wrapper=$2
    grep -Fq "$NOTICE" "$wrapper" && grep -Fxq "$EXIT_LINE" "$wrapper"
    exit
fi

wrapper=$1
docker_image_ref=$2
cat > "$wrapper" << WRAPPER
#!/usr/bin/env bash
echo "$NOTICE To keep using the software please use a different version. If you absolutely need this container for reproducibility you can pull it from docker hub via the command apptainer pull docker://vnmd/$docker_image_ref" >&2
$EXIT_LINE
WRAPPER
chmod +x "$wrapper"
