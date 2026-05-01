#!/bin/bash
# Entrypoint for the neurocommand CLI image.
#
# Probes regional CVMFS servers, picks the lowest-latency Direct/CDN config,
# starts autofs (or falls back to a manual mount), then execs the user command.
#
# Env knobs:
#   NEURODESK_CVMFS_DISABLE=1       skip CVMFS entirely (offline / local-only mode)
#   NEURODESK_SKIP_REGION_PROBE=1   use the bundled default config without probing

set -e

NB_USER="${NB_USER:-jovyan}"

setup_cvmfs() {
    if [ "${NEURODESK_CVMFS_DISABLE:-0}" = "1" ]; then
        echo "[neurocommand] CVMFS disabled by NEURODESK_CVMFS_DISABLE=1"
        return 0
    fi

    if [ ! -e /dev/fuse ]; then
        echo "[neurocommand] /dev/fuse missing - run with --device /dev/fuse (or --privileged). Skipping CVMFS."
        return 0
    fi

    if ! timeout 3 nslookup neurodesk.org >/dev/null 2>&1; then
        echo "[neurocommand] No internet connectivity. Skipping CVMFS."
        return 0
    fi

    if [ -d "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/" ]; then
        echo "[neurocommand] CVMFS already mounted."
        return 0
    fi

    CACHE_DIR="/home/${NB_USER}/cvmfs_cache"
    mkdir -p "$CACHE_DIR"
    chown -R cvmfs:root "$CACHE_DIR" 2>/dev/null || true
    chmod 755 "/home/${NB_USER}"

    if [ "${NEURODESK_SKIP_REGION_PROBE:-0}" != "1" ]; then
        probe_and_select_region
    fi

    echo "[neurocommand] Mounting CVMFS..."
    if [ -x /etc/init.d/autofs ] && service autofs start >/dev/null 2>&1; then
        ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ >/dev/null 2>&1 \
            && echo "[neurocommand] CVMFS ready (autofs)." \
            || mount_cvmfs_manually
    else
        mount_cvmfs_manually
    fi
}

mount_cvmfs_manually() {
    mkdir -p /cvmfs/neurodesk.ardc.edu.au
    if mount -t cvmfs neurodesk.ardc.edu.au /cvmfs/neurodesk.ardc.edu.au 2>&1; then
        ls /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/ >/dev/null 2>&1 \
            && echo "[neurocommand] CVMFS ready (manual mount)." \
            || echo "[neurocommand] WARN: CVMFS mounted but modules dir not visible yet."
    else
        echo "[neurocommand] WARN: manual CVMFS mount failed. Falling back to local containers."
    fi
}

probe_and_select_region() {
    # Median of 3 latency probes against .cvmfspublished.
    get_latency() {
        local url="$1" probes=3 i out t s
        local latencies=()
        for i in $(seq 1 "$probes"); do
            out=$(curl --no-keepalive --connect-timeout 3 -s -w "%{time_total} %{http_code}" -o /dev/null "$url")
            t=$(echo "$out" | awk '{print $1}')
            s=$(echo "$out" | awk '{print $2}')
            if [ "$s" = "200" ]; then latencies+=("$t"); else latencies+=("999"); fi
        done
        printf '%s\n' "${latencies[@]}" | sort -n | sed -n "$(( (probes + 1) / 2 ))p"
    }

    local tmpdir; tmpdir=$(mktemp -d)
    (get_latency "http://cvmfs-frankfurt.neurodesk.org/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished" > "$tmpdir/europe") &
    (get_latency "http://cvmfs-jetstream.neurodesk.org/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished" > "$tmpdir/america") &
    (get_latency "http://cvmfs-brisbane.neurodesk.org/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished" > "$tmpdir/asia") &
    wait

    local eu am as
    eu=$(cat "$tmpdir/europe"); am=$(cat "$tmpdir/america"); as=$(cat "$tmpdir/asia")
    rm -rf "$tmpdir"
    echo "[neurocommand] Latencies (s): europe=$eu america=$am asia=$as"

    local fastest_region
    fastest_region=$(printf "%s europe\n%s america\n%s asia\n" "$eu" "$am" "$as" \
        | sort -n | awk 'NR==1{print $2}')

    local direct_lat cdn_lat fastest_mode
    direct_lat=$(get_latency "http://cvmfs-geoproximity.neurodesk.org/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished")
    cdn_lat=$(get_latency "http://cvmfs.neurodesk.org/cvmfs/neurodesk.ardc.edu.au/.cvmfspublished")
    fastest_mode=$(printf "%s direct\n%s cdn\n" "$direct_lat" "$cdn_lat" \
        | sort -n | awk 'NR==1{print $2}')

    echo "[neurocommand] Selected region=${fastest_region} mode=${fastest_mode}"
    local src="/etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.${fastest_mode}.${fastest_region}"
    if [ -f "$src" ]; then
        cp "$src" /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf
    else
        echo "[neurocommand] WARN: $src not found, keeping default config."
    fi
}

setup_cvmfs

if [ "$(id -u)" = "0" ] && command -v gosu >/dev/null 2>&1; then
    if [ $# -eq 0 ]; then exec gosu "${NB_USER}" /bin/bash -l
    else exec gosu "${NB_USER}" "$@"
    fi
else
    if [ $# -eq 0 ]; then exec /bin/bash -l
    else exec "$@"
    fi
fi
