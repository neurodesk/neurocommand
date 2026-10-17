#!/usr/bin/env bash
set -euo pipefail
umask 077

source .github/workflows/upload_containers_simg.sh

image=mrtrix3src_latest_20261017.simg
expected_sha=515df44bb893fc086493864fa5be5a418ed02dfad820029209ce9b0a17ba790d
recovery="${IMAGE_HOME}/nectar-997-repair"
mkdir -p "$recovery"
nectar_url="${NECTAR_BASE_URL}/${image}"
aws_url="${AWS_BASE_URL}/${image}"

swift_client stat neurodesk >/dev/null

if [[ ! -f "$recovery/manifest.before.json" ]]; then
    curl --fail --silent --show-error --max-time 60 \
        "${nectar_url}?multipart-manifest=get" \
        --output "$recovery/manifest.before.json.tmp"
    mv "$recovery/manifest.before.json.tmp" "$recovery/manifest.before.json"
fi

if curl --fail --silent --show-error --location --range 0-0 \
        --max-filesize 1 --connect-timeout 15 --max-time 60 \
        --output /dev/null "$nectar_url" &&
   curl --fail --silent --show-error --location \
        --connect-timeout 15 --max-time 3600 \
        --output "$recovery/public-verified.simg" "$nectar_url" &&
   printf '%s  %s\n' "$expected_sha" "$recovery/public-verified.simg" |
    sha256sum --check --status; then
    echo '[INFO] Public Nectar download already matches the expected image; upload skipped.'
    exit 0
fi

if ! printf '%s  %s\n' "$expected_sha" "$recovery/$image" |
        sha256sum --check --status; then
    curl --fail --silent --show-error --location \
        --retry 5 --retry-delay 10 --connect-timeout 15 --max-time 3600 \
        --output "$recovery/$image.download" "$aws_url"
    printf '%s  %s\n' "$expected_sha" "$recovery/$image.download" |
        sha256sum --check
    mv "$recovery/$image.download" "$recovery/$image"
fi
validate_singularity_image "$recovery/$image"
run_with_retries "repair Nectar image" "$SWIFT_UPLOAD_RETRIES" "$SWIFT_UPLOAD_RETRY_DELAY" \
    swift_client upload --use-slo --leave-segments \
    --segment-size "$SWIFT_SEGMENT_SIZE" --segment-container neurodesk \
    --object-name "$image" neurodesk "$recovery/$image"

curl --fail --silent --show-error --location \
    --retry 5 --retry-delay 10 --connect-timeout 15 --max-time 3600 \
    --output "$recovery/public-verified.simg" "$nectar_url"
printf '%s  %s\n' "$expected_sha" "$recovery/public-verified.simg" |
    sha256sum --check
echo '[INFO] Anonymous full Nectar download matches the expected image SHA-256.'
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    echo 'Repaired Nectar object and verified its complete anonymous download against the source SHA-256.' >> "$GITHUB_STEP_SUMMARY"
fi
