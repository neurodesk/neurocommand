#!/usr/bin/env bash

# Ensure that the CVMFS publisher can execute a foreign-architecture container
# while transparent-singularity discovers its commands and environment.

BINFMT_MISC_DIR="${BINFMT_MISC_DIR:-/proc/sys/fs/binfmt_misc}"
BINFMT_AUTO_INSTALL="${BINFMT_AUTO_INSTALL:-1}"
BINFMT_INSTALL_IMAGE="${BINFMT_INSTALL_IMAGE:-docker.io/tonistiigi/binfmt:qemu-v10.2.3@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0}"

normalize_architecture() {
    case "$1" in
        aarch64|arm64)
            echo "aarch64"
            ;;
        x86_64|amd64)
            echo "x86_64"
            ;;
        *)
            echo "$1"
            ;;
    esac
}

container_target_architecture() {
    local container_name="$1"
    local stem name_and_version image_name

    stem="${container_name%.simg}"
    name_and_version="${stem%_*}"
    image_name="${name_and_version%_*}"

    case "$image_name:$name_and_version" in
        *_arm64:*|*_aarch64:*|*:*_arm64|*:*_aarch64)
            echo "aarch64"
            ;;
        *_amd64:*|*_x86_64:*|*:*_amd64|*:*_x86_64)
            echo "x86_64"
            ;;
        *)
            # Images without an architecture suffix use the publisher's native
            # architecture and do not require emulation.
            normalize_architecture "${HOST_ARCH_OVERRIDE:-$(uname -m)}"
            ;;
    esac
}

binfmt_handler_name() {
    case "$1" in
        aarch64)
            echo "qemu-aarch64"
            ;;
        x86_64)
            echo "qemu-x86_64"
            ;;
        *)
            return 1
            ;;
    esac
}

run_privileged() {
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "[ERROR] Root privileges are required to install/register binfmt handlers." >&2
        return 1
    fi
}

ensure_binfmt_misc_mounted() {
    if [[ -f "$BINFMT_MISC_DIR/register" ]]; then
        return 0
    fi

    echo "[INFO] Mounting binfmt_misc at $BINFMT_MISC_DIR."
    run_privileged mkdir -p "$BINFMT_MISC_DIR" || return 1
    run_privileged mount -t binfmt_misc binfmt_misc "$BINFMT_MISC_DIR"
}

binfmt_handler_ready() {
    local target_arch="$1"
    local handler entry interpreter flags

    handler="$(binfmt_handler_name "$target_arch")" || return 1
    entry="$BINFMT_MISC_DIR/$handler"
    [[ -r "$entry" ]] || return 1
    [[ "$(head -n 1 "$entry" 2>/dev/null)" == "enabled" ]] || return 1

    interpreter="$(awk -F' ' '$1 == "interpreter" {print $2}' "$entry")"
    flags="$(awk -F': ' '$1 == "flags" {print $2}' "$entry")"

    # Apptainer executes inside a mount namespace. The fix-binary flag keeps
    # the interpreter open and usable after the container root filesystem is
    # entered. A container-based registrar may leave its interpreter path
    # invisible on the host, but the pinned open file remains valid with F.
    [[ -n "$interpreter" && "$flags" == *F* ]]
}

install_binfmt_with_container_runtime() {
    local target_arch="$1"
    local install_arch runtime

    case "$target_arch" in
        aarch64)
            install_arch="arm64"
            ;;
        x86_64)
            install_arch="amd64"
            ;;
        *)
            return 1
            ;;
    esac

    if command -v podman >/dev/null 2>&1; then
        runtime="podman"
    elif command -v docker >/dev/null 2>&1; then
        runtime="docker"
    elif command -v dnf >/dev/null 2>&1; then
        echo "[INFO] Installing Podman for the binfmt installer fallback."
        run_privileged dnf install -y podman || return 1
        runtime="podman"
    elif command -v yum >/dev/null 2>&1; then
        echo "[INFO] Installing Podman for the binfmt installer fallback."
        run_privileged yum install -y podman || return 1
        runtime="podman"
    else
        echo "[ERROR] Neither podman nor docker is available, and Podman cannot be installed with dnf/yum." >&2
        return 1
    fi

    echo "[INFO] Registering $target_arch with the pinned binfmt installer image."
    run_privileged "$runtime" run --privileged --rm \
        "$BINFMT_INSTALL_IMAGE" --install "$install_arch"
}

install_foreign_arch_support() {
    local target_arch="$1"

    echo "[INFO] Installing QEMU user emulation and binfmt support for $target_arch."
    ensure_binfmt_misc_mounted || return 1

    if command -v apt-get >/dev/null 2>&1; then
        run_privileged apt-get update || return 1
        run_privileged env DEBIAN_FRONTEND=noninteractive \
            apt-get install -y qemu-user-static binfmt-support || return 1
    elif command -v dnf >/dev/null 2>&1; then
        run_privileged dnf install -y qemu-user-static || return 1
    elif command -v yum >/dev/null 2>&1; then
        run_privileged yum install -y qemu-user-static || return 1
    else
        echo "[ERROR] No supported package manager found (apt-get, dnf, or yum)." >&2
        return 1
    fi

    # Re-apply registrations installed by either binfmt-support or systemd.
    if command -v update-binfmts >/dev/null 2>&1; then
        run_privileged update-binfmts --enable "$(binfmt_handler_name "$target_arch")" || return 1
    elif [[ -x /usr/lib/systemd/systemd-binfmt ]]; then
        run_privileged /usr/lib/systemd/systemd-binfmt || return 1
    elif command -v systemctl >/dev/null 2>&1; then
        run_privileged systemctl restart systemd-binfmt.service || return 1
    else
        echo "[ERROR] QEMU was installed, but no binfmt registration tool was found." >&2
        return 1
    fi
}

ensure_container_architecture_support() {
    local container_name="$1"
    local host_arch target_arch handler

    host_arch="$(normalize_architecture "${HOST_ARCH_OVERRIDE:-$(uname -m)}")"
    target_arch="$(container_target_architecture "$container_name")"

    if [[ "$target_arch" == "$host_arch" ]]; then
        return 0
    fi

    handler="$(binfmt_handler_name "$target_arch")" || {
        echo "[ERROR] Unsupported foreign container architecture '$target_arch' for $container_name." >&2
        return 1
    }

    if binfmt_handler_ready "$target_arch"; then
        echo "[INFO] Foreign-architecture support is ready: $handler ($host_arch -> $target_arch)."
        return 0
    fi

    if [[ "$BINFMT_AUTO_INSTALL" != "1" ]]; then
        echo "[ERROR] $handler is not registered with an interpreter and the F flag." >&2
        echo "[ERROR] Set BINFMT_AUTO_INSTALL=1 or install qemu-user-static/binfmt-support manually." >&2
        return 1
    fi

    if ! install_foreign_arch_support "$target_arch" || ! binfmt_handler_ready "$target_arch"; then
        echo "[WARNING] System-package registration did not provide $handler; trying the container installer."
        install_binfmt_with_container_runtime "$target_arch" || {
            echo "[ERROR] Failed to install QEMU/binfmt support for $target_arch." >&2
            return 1
        }
    fi

    if ! binfmt_handler_ready "$target_arch"; then
        echo "[ERROR] $handler is still unavailable after installation; expected an enabled handler with the F flag." >&2
        return 1
    fi

    echo "[INFO] Foreign-architecture support installed: $handler ($host_arch -> $target_arch)."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [[ $# -ne 1 ]]; then
        echo "Usage: $0 CONTAINER_NAME_VERSION_BUILDDATE[.simg]" >&2
        exit 2
    fi
    ensure_container_architecture_support "$1"
fi
