#!/usr/bin/env bash
set -euo pipefail

NECTAR_BASE_URL="https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk"
AWS_BASE_URL="https://neurocontainers.s3.us-east-2.amazonaws.com"

IMAGE_HOME="${IMAGE_HOME:-/storage/tmp}"
SINGULARITY_BUILD_RETRIES="${SINGULARITY_BUILD_RETRIES:-3}"
SINGULARITY_BUILD_RETRY_DELAY="${SINGULARITY_BUILD_RETRY_DELAY:-60}"
SINGULARITY_BUILD_TIMEOUT="${SINGULARITY_BUILD_TIMEOUT:-2h}"
RCLONE_COPY_RETRIES="${RCLONE_COPY_RETRIES:-3}"
RCLONE_COPY_RETRY_DELAY="${RCLONE_COPY_RETRY_DELAY:-30}"
RCLONE_LOW_LEVEL_RETRIES="${RCLONE_LOW_LEVEL_RETRIES:-20}"
SWIFT_UPLOAD_RETRIES="${SWIFT_UPLOAD_RETRIES:-3}"
SWIFT_UPLOAD_RETRY_DELAY="${SWIFT_UPLOAD_RETRY_DELAY:-30}"
SWIFT_SEGMENT_SIZE="${SWIFT_SEGMENT_SIZE:-1073741824}"
SWIFT_REQUEST_TIMEOUT="${SWIFT_REQUEST_TIMEOUT:-60}"
DOWNLOAD_RETRIES="${DOWNLOAD_RETRIES:-3}"
DOWNLOAD_RETRY_DELAY="${DOWNLOAD_RETRY_DELAY:-30}"
URL_CHECK_RETRIES="${URL_CHECK_RETRIES:-5}"
URL_CHECK_RETRY_DELAY="${URL_CHECK_RETRY_DELAY:-15}"
CURL_CONNECT_TIMEOUT="${CURL_CONNECT_TIMEOUT:-15}"

run_with_retries() {
    local description="$1"
    local attempts="$2"
    local delay_seconds="$3"
    shift 3

    local attempt exit_code
    exit_code=1
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        echo "[DEBUG] ${description} (attempt ${attempt}/${attempts})"
        if "$@"; then
            return 0
        else
            exit_code=$?
        fi
        echo "[WARNING] ${description} failed with exit code ${exit_code}"
        if [[ $attempt -lt $attempts ]]; then
            echo "[DEBUG] Retrying ${description} in ${delay_seconds}s"
            sleep "$delay_seconds"
        fi
    done

    echo "[ERROR] ${description} failed after ${attempts} attempts"
    return "$exit_code"
}

url_size() {
    local url="$1"
    local retry_count probe size received_size
    retry_count=$((URL_CHECK_RETRIES > 0 ? URL_CHECK_RETRIES - 1 : 0))
    probe="$(mktemp -d)" || return 1

    # HEAD can succeed for a Swift manifest whose public GET fails. Sample the
    # first byte; this does not verify every segment of a large object.
    # Cap ignored ranges with head; --max-filesize also rejects 503 error
    # bodies before curl can retry them.
    if ! curl --silent --fail --range 0-0 \
        --connect-timeout "$CURL_CONNECT_TIMEOUT" \
        --max-time "$SWIFT_REQUEST_TIMEOUT" \
        --retry "$retry_count" \
        --retry-delay "$URL_CHECK_RETRY_DELAY" \
        --retry-connrefused \
        --dump-header "$probe/headers" \
        "$url" | head -c 2 > "$probe/body"; then
        rm -rf "$probe"
        return 1
    fi
    received_size="$(stat -c %s "$probe/body")"
    size="$(awk '
        /^HTTP\// {status=$2; size=""}
        tolower($1) == "content-range:" && $2 == "bytes" {
            gsub("\r", "", $3)
            if ($3 ~ /^0-0\/[1-9][0-9]*$/) {size=substr($3, 5)}
        }
        END {if (status == 206) print size}
    ' "$probe/headers")"
    rm -rf "$probe"
    [[ "$received_size" == 1 && "$size" =~ ^[1-9][0-9]*$ ]] || return 1
    echo "$size"
}

url_exists() {
    url_size "$1" >/dev/null
}

url_matches_size() {
    local url="$1"
    local expected_size="$2" actual_size
    actual_size="$(url_size "$url")" || return 1
    [[ "$actual_size" == "$expected_size" ]]
}

# Image validation needs "sif list" (Singularity 3.3+, every Apptainer).
container_runtime() {
    local runtime
    for runtime in singularity apptainer; do
        if command -v "$runtime" >/dev/null 2>&1 &&
            "$runtime" sif list --help >/dev/null 2>&1; then
            echo "$runtime"
            return 0
        fi
    done
    return 1
}

ensure_container_runtime() {
    if container_runtime >/dev/null 2>&1; then
        return 0
    fi

    if [ -n "${singularity_setup_done:-}" ]; then
        echo "Setup already done. Skipping."
    else
        # install apptainer
        sudo apt update > /dev/null 2>&1
        sudo apt install -y software-properties-common > /dev/null 2>&1
        sudo add-apt-repository -y ppa:apptainer/ppa > /dev/null 2>&1
        sudo apt update > /dev/null 2>&1
        sudo apt install -y apptainer apptainer-suid > /dev/null 2>&1

        export singularity_setup_done="true"
    fi

    container_runtime >/dev/null
}

with_timeout() {
    local timeout_duration="$1"
    shift

    if [[ -n "$timeout_duration" ]] && command -v timeout >/dev/null 2>&1; then
        timeout "$timeout_duration" "$@"
    else
        "$@"
    fi
}

validate_singularity_image() {
    local image_path="$1"
    if [[ ! -s "$image_path" ]]; then
        echo "[ERROR] ${image_path} does not exist or is empty"
        return 1
    fi

    ensure_container_runtime
    local runtime listing position data_end=0 size
    runtime="$(container_runtime)"
    # inspect refuses images built for another architecture, such as arm64
    # images on the amd64 runner. The SIF descriptor table is architecture
    # independent: it must parse, and the file must hold every data object.
    # Compare in bash: its 64-bit integers hold offsets beyond 2 GiB.
    listing="$("$runtime" sif list "$image_path")" || return 1
    while IFS='|' read -r _ _ _ position _; do
        position="${position// /}"
        if [[ "$position" =~ ^[0-9]+-([0-9]+)$ ]] &&
            (( BASH_REMATCH[1] > data_end )); then
            data_end="${BASH_REMATCH[1]}"
        fi
    done <<< "$listing"
    size="$(stat -c %s "$image_path")" || return 1
    if (( data_end == 0 || size < data_end )); then
        echo "[ERROR] ${image_path} is not a complete SIF image"
        return 1
    fi
}

download_image_once() {
    local url="$1"
    local temp_path="$2"

    rm -f "$temp_path"
    curl --fail --location --connect-timeout "$CURL_CONNECT_TIMEOUT" \
        --output "$temp_path" \
        "$url"
    validate_singularity_image "$temp_path"
}

download_image() {
    local url="$1"
    local target_path="$2"
    local temp_path="${target_path}.download.$$"

    if run_with_retries "download ${url}" "$DOWNLOAD_RETRIES" "$DOWNLOAD_RETRY_DELAY" download_image_once "$url" "$temp_path"; then
        mv "$temp_path" "$target_path"
    else
        rm -f "$temp_path"
        return 1
    fi
}

ensure_free_space() {
    local free
    free=$(df -k --output=avail "$PWD" | tail -n1)
    echo "[DEBUG] This runner has ${free} free disk space"
    if [[ $free -lt 20485760 ]]; then               # 20G = 20*1024*1024k
        echo "[DEBUG] This runner has not enough free disk space .. cleaning up!"
        bash .github/workflows/free-up-space.sh
        free=$(df -k --output=avail "$PWD" | tail -n1)
        echo "[DEBUG] This runner has ${free} free disk space after cleanup"
    fi
}

build_singularity_image_once() {
    local temp_path="$1"
    local image_name="$2"
    local build_date="$3"
    local runtime

    rm -f "$temp_path"
    runtime="$(container_runtime)"
    echo "[DEBUG] ${runtime} building docker://vnmd/${image_name}:${build_date}"
    with_timeout "$SINGULARITY_BUILD_TIMEOUT" "$runtime" build "$temp_path" "docker://vnmd/${image_name}:${build_date}"
    validate_singularity_image "$temp_path"
}

build_singularity_image() {
    local image_builddate="$1"
    local image_name="$2"
    local build_date="$3"
    local target_path="$IMAGE_HOME/${image_builddate}.simg"
    local temp_path="${target_path}.tmp.$$"

    ensure_free_space
    ensure_container_runtime

    if run_with_retries "build docker://vnmd/${image_name}:${build_date}" "$SINGULARITY_BUILD_RETRIES" "$SINGULARITY_BUILD_RETRY_DELAY" build_singularity_image_once "$temp_path" "$image_name" "$build_date"; then
        mv "$temp_path" "$target_path"
    else
        rm -f "$temp_path"
        return 1
    fi
}

ensure_valid_local_image() {
    local image_path="$1"

    if [[ ! -f "$image_path" ]]; then
        return 1
    fi

    echo "[DEBUG] Found ${image_path} in local cache; validating it"
    if validate_singularity_image "$image_path"; then
        echo "[DEBUG] ${image_path} passed validation"
        return 0
    fi

    echo "[WARNING] ${image_path} failed validation; removing it"
    rm -f "$image_path"
    return 1
}

rclone_copy_image() {
    local image_path="$1"
    local remote_path="$2"
    local description="$3"

    run_with_retries "$description" "$RCLONE_COPY_RETRIES" "$RCLONE_COPY_RETRY_DELAY" \
        rclone copy \
        --retries "$RCLONE_COPY_RETRIES" \
        --low-level-retries "$RCLONE_LOW_LEVEL_RETRIES" \
        --checksum \
        "$image_path" \
        "$remote_path"
}

swift_client() {
    local -a auth_options=()
    if [[ -n "${OS_APPLICATION_CREDENTIAL_ID:-}" && -n "${OS_APPLICATION_CREDENTIAL_SECRET:-}" ]]; then
        auth_options=(--os-auth-type v3applicationcredential)
    fi
    swift "${auth_options[@]}" --timeout "$SWIFT_REQUEST_TIMEOUT" "$@"
}

upload_nectar_image() {
    local image_path="$1"

    # DLO sizes depend on eventually consistent segment listings. SLO checks
    # each segment's size and checksum before publishing a complete manifest.
    # Keep segments in the public container so anonymous downloads can read them.
    run_with_retries "upload ${image_path##*/} to Nectar" "$SWIFT_UPLOAD_RETRIES" "$SWIFT_UPLOAD_RETRY_DELAY" \
        swift_client upload --use-slo --leave-segments \
        --segment-size "$SWIFT_SEGMENT_SIZE" --segment-container neurodesk \
        --object-name "${image_path##*/}" neurodesk "$image_path"
}

ensure_released() {
    local image_builddate="$1"
    local image_path="$IMAGE_HOME/${image_builddate}.simg"
    local nectar_url="${NECTAR_BASE_URL}/${image_builddate}.simg"
    local aws_url="${AWS_BASE_URL}/${image_builddate}.simg"
    local image_size
    image_size="$(stat -c %s "$image_path")"

    if ! url_matches_size "$nectar_url" "$image_size"; then
        upload_nectar_image "$image_path"
    fi

    if ! url_matches_size "$aws_url" "$image_size"; then
        rclone_copy_image "$image_path" aws-neurocontainers-new:/neurocontainers/ "upload ${image_builddate}.simg to AWS"
    fi

    run_with_retries "confirm Nectar image download" "$URL_CHECK_RETRIES" "$URL_CHECK_RETRY_DELAY" \
        url_matches_size "$nectar_url" "$image_size"
    run_with_retries "confirm AWS image download" "$URL_CHECK_RETRIES" "$URL_CHECK_RETRY_DELAY" \
        url_matches_size "$aws_url" "$image_size"
    echo "[DEBUG] ${image_builddate}.simg is released in Nectar and AWS"
}

process_container_line() {
    local line="$1"
    echo "LINE: $line"
    local image_builddate
    image_builddate="$(cut -d' ' -f1 <<< "$line")"
    echo "IMAGENAME_BUILDDATE: $image_builddate"

    local image_name build_date image_path nectar_url aws_url
    image_name="${image_builddate%_*}"
    build_date="${image_builddate##*_}"
    image_path="$IMAGE_HOME/${image_builddate}.simg"
    nectar_url="${NECTAR_BASE_URL}/${image_builddate}.simg"
    aws_url="${AWS_BASE_URL}/${image_builddate}.simg"

    echo "[DEBUG] IMAGENAME: $image_name"
    echo "[DEBUG] BUILDDATE: $build_date"

    local nectar_size aws_size
    nectar_size="$(url_size "$nectar_url")" || nectar_size=
    aws_size="$(url_size "$aws_url")" || aws_size=
    if [[ -n "$aws_size" && "$nectar_size" == "$aws_size" ]]; then
        echo "[DEBUG] ${image_builddate}.simg exists in Nectar and AWS"
        return 0
    fi

    echo "[DEBUG] ${image_builddate}.simg is unavailable or has inconsistent sizes across object stores"
    if ! ensure_valid_local_image "$image_path"; then
        if url_exists "$aws_url"; then
            echo "[DEBUG] ${image_builddate}.simg exists in AWS; downloading to local cache"
            download_image "$aws_url" "$image_path"
        elif url_exists "$nectar_url"; then
            echo "[DEBUG] ${image_builddate}.simg exists in Nectar; downloading to local cache"
            download_image "$nectar_url" "$image_path"
        else
            echo "[DEBUG] ${image_builddate}.simg does not exist in any cloud object storage"
            echo "[DEBUG] Rebuilding from docker"
            build_singularity_image "$image_builddate" "$image_name" "$build_date"
        fi
    fi

    ensure_released "$image_builddate"
    echo "[DEBUG] Cleaning up local cache for ${image_builddate}.simg"
    rm -f "$image_path"
}

main() {
    echo "checking if containers are built"

    run_with_retries "check Nectar credentials" "$SWIFT_UPLOAD_RETRIES" "$SWIFT_UPLOAD_RETRY_DELAY" \
        swift_client stat neurodesk >/dev/null

    local inventory
    inventory="$(mktemp)"
    # shellcheck disable=SC2064 # Expand the path now; the local is gone at exit.
    trap "rm -f '$inventory'" EXIT
    python3 cvmfs/generate_metadata.py --log "$inventory"

    echo "[debug] container inventory:"
    cat "$inventory"

    mkdir -p "$IMAGE_HOME"

    mapfile -t arr < "$inventory"
    for line in "${arr[@]}"; do
        process_container_line "$line"
    done
    # The workflow's publish_metadata job commits cvmfs/log.txt and
    # cvmfs/applist.json for this revision only after this script succeeds.
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
