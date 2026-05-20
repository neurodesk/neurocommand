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
        fi

        exit_code=$?
        echo "[WARNING] ${description} failed with exit code ${exit_code}"
        if [[ $attempt -lt $attempts ]]; then
            echo "[DEBUG] Retrying ${description} in ${delay_seconds}s"
            sleep "$delay_seconds"
        fi
    done

    echo "[ERROR] ${description} failed after ${attempts} attempts"
    return "$exit_code"
}

url_exists_once() {
    local url="$1"
    curl --output /dev/null --silent --head --fail \
        --connect-timeout "$CURL_CONNECT_TIMEOUT" \
        "$url"
}

url_exists() {
    url_exists_once "$1"
}

url_exists_with_retries() {
    local url="$1"
    run_with_retries "confirm URL exists: ${url}" "$URL_CHECK_RETRIES" "$URL_CHECK_RETRY_DELAY" url_exists_once "$url"
}

container_runtime() {
    if command -v singularity >/dev/null 2>&1; then
        echo "singularity"
    elif command -v apptainer >/dev/null 2>&1; then
        echo "apptainer"
    else
        return 1
    fi
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
    local runtime
    runtime="$(container_runtime)"
    "$runtime" inspect "$image_path" > /dev/null
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

ensure_released() {
    local image_builddate="$1"
    local image_path="$IMAGE_HOME/${image_builddate}.simg"
    local nectar_url="${NECTAR_BASE_URL}/${image_builddate}.simg"
    local aws_url="${AWS_BASE_URL}/${image_builddate}.simg"

    if ! url_exists "$nectar_url"; then
        rclone_copy_image "$image_path" nectar:/neurodesk/ "upload ${image_builddate}.simg to Nectar"
    fi

    if ! url_exists "$aws_url"; then
        rclone_copy_image "$image_path" aws-neurocontainers-new:/neurocontainers/ "upload ${image_builddate}.simg to AWS"
    fi

    url_exists_with_retries "$nectar_url"
    url_exists_with_retries "$aws_url"
    echo "[DEBUG] ${image_builddate}.simg is released in Nectar and AWS"
}

process_container_line() {
    local line="$1"
    echo "LINE: $line"
    local image_builddate
    image_builddate="$(cut -d' ' -f1 <<< "$line")"
    echo "IMAGENAME_BUILDDATE: $image_builddate"

    local image_name build_date image_path nectar_url aws_url
    image_name="$(cut -d'_' -f1,2 <<< "$image_builddate")"
    build_date="$(cut -d'_' -f3 <<< "$image_builddate")"
    image_path="$IMAGE_HOME/${image_builddate}.simg"
    nectar_url="${NECTAR_BASE_URL}/${image_builddate}.simg"
    aws_url="${AWS_BASE_URL}/${image_builddate}.simg"

    echo "[DEBUG] IMAGENAME: $image_name"
    echo "[DEBUG] BUILDDATE: $build_date"

    if url_exists "$nectar_url" && url_exists "$aws_url"; then
        echo "[DEBUG] ${image_builddate}.simg exists in Nectar and AWS"
        return 0
    fi

    echo "[DEBUG] ${image_builddate}.simg is missing from one or more object stores"
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

    # creating logfile with available containers
    python3 neurodesk/write_log.py
    pip3 install requests

    # remove empty lines
    sed -i '/^$/d' log.txt

    # remove square brackets
    sed -i 's/[][]//g' log.txt

    # remove spaces around
    sed -i -e 's/^[ \t]*//' -e 's/[ \t]*$//' log.txt

    echo "[debug] logfile:"
    cat log.txt
    echo "[debug] logfile is at: $PWD"

    mkdir -p "$IMAGE_HOME"

    mapfile -t arr < log.txt
    for line in "${arr[@]}"; do
        process_container_line "$line"
    done

    # once everything is uploaded successfully move log file to cvmfs folder, so cvmfs can start downloading the containers:
    echo "[Debug] mv logfile to cvmfs directory"
    mv log.txt cvmfs

    cd cvmfs
    echo "[Debug] generate applist.json file for website"
    python json_gen.py --apps-json ../neurodesk/apps.json # this generates the applist.json for the website
    # these files will be committed via uses: stefanzweifel/git-auto-commit-action@v4
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
