# syntax=docker/dockerfile:1.7
#
# Neurodesk for Command Line: CVMFS, Lmod, Apptainer and neurocommand modules.

ARG BASE_IMAGE_TAG=2026-04-27
ARG APPTAINER_VERSION=1.4.5

FROM ghcr.io/apptainer/apptainer:${APPTAINER_VERSION} AS apptainer

FROM quay.io/jupyter/docker-stacks-foundation:${BASE_IMAGE_TAG}
ARG BASE_IMAGE_TAG

# foundation drops to NB_USER (jovyan, UID 1000) at the end. We need root for installs.
USER root

ENV LANG="" LANGUAGE="" LC_ALL=""
ENV DEBIAN_FRONTEND=noninteractive

# CVMFS is unhappy with the foundation image's stub systemctl; replace with a no-op.
RUN if [ -f /usr/bin/systemctl ]; then mv /usr/bin/systemctl /usr/bin/systemctl.orig; fi \
    && printf '#!/bin/bash\necho "systemctl is disabled in this container"\n' > /usr/bin/systemctl \
    && chmod +x /usr/bin/systemctl

# CVMFS (CERN apt repo) + Lmod + minimal CLI tooling.
RUN apt_update_with_candidates() { \
        packages="$*"; \
        for attempt in 1 2 3 4 5; do \
            rm -rf /var/lib/apt/lists/*; \
            if apt-get update --yes -o Acquire::Retries=5; then \
                missing=""; \
                for package in ${packages}; do \
                    candidate="$(apt-cache policy "${package}" | awk '/Candidate:/ { print $2 }')"; \
                    if [ -z "${candidate}" ] || [ "${candidate}" = "(none)" ]; then \
                        missing="${missing} ${package}"; \
                    fi; \
                done; \
                if [ -z "${missing}" ]; then \
                    return 0; \
                fi; \
                echo "apt indexes are missing package candidates:${missing}"; \
            fi; \
            echo "apt update attempt ${attempt} failed; retrying"; \
            sleep $((attempt * 10)); \
        done; \
        return 1; \
    }; \
    apt_update_with_candidates ca-certificates wget curl gnupg \
    && apt-get install --yes --no-install-recommends \
        ca-certificates wget curl gnupg \
    && wget -q https://cvmrepo.s3.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb -O /tmp/cvmfs-release.deb \
    && dpkg -i /tmp/cvmfs-release.deb \
    && rm /tmp/cvmfs-release.deb \
    && apt_update_with_candidates \
        autofs cvmfs uuid-dev \
        lmod lua5.2 lua-bit32 lua-filesystem lua-json lua-lpeg lua-posix lua-term \
        tcllib tk \
        squashfs-tools \
        sudo \
        bc dnsutils git less nano openssh-client rsync tree unzip vim zip \
    && apt-get install --yes --no-install-recommends \
        autofs cvmfs uuid-dev \
        lmod lua5.2 lua-bit32 lua-filesystem lua-json lua-lpeg lua-posix lua-term \
        tcllib tk \
        squashfs-tools \
        sudo \
        bc dnsutils git less nano openssh-client rsync tree unzip vim zip \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /root/.cache

# Install apptainer from its official multi-arch image. This avoids the
# Launchpad API and PPA apt fetches, which have been flaky in scheduled builds.
COPY --from=apptainer /opt/apptainer /opt/apptainer
RUN ln -sf /opt/apptainer/bin/apptainer /usr/local/bin/apptainer \
    && ln -sf /opt/apptainer/bin/singularity /usr/local/bin/singularity

# CVMFS client config. Single source of truth lives in neurodesktop; pin a ref
# (or override at build time) to fetch a known-good snapshot.
ARG NEURODESKTOP_CVMFS_REF=main
ARG NEURODESKTOP_RAW=https://raw.githubusercontent.com/neurodesk/neurodesktop/${NEURODESKTOP_CVMFS_REF}/config/cvmfs

ADD ${NEURODESKTOP_RAW}/neurodesk.ardc.edu.au.pub                  /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub
ADD ${NEURODESKTOP_RAW}/default.local                              /etc/cvmfs/default.local
ADD ${NEURODESKTOP_RAW}/neurodesk.ardc.edu.au.conf.cdn.america     /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.cdn.america
ADD ${NEURODESKTOP_RAW}/neurodesk.ardc.edu.au.conf.cdn.asia        /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.cdn.asia
ADD ${NEURODESKTOP_RAW}/neurodesk.ardc.edu.au.conf.cdn.europe      /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.cdn.europe
ADD ${NEURODESKTOP_RAW}/neurodesk.ardc.edu.au.conf.direct.america  /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.direct.america
ADD ${NEURODESKTOP_RAW}/neurodesk.ardc.edu.au.conf.direct.asia     /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.direct.asia
ADD ${NEURODESKTOP_RAW}/neurodesk.ardc.edu.au.conf.direct.europe   /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.direct.europe

RUN chmod 0644 /etc/cvmfs/keys/ardc.edu.au/neurodesk.ardc.edu.au.pub \
              /etc/cvmfs/default.local \
              /etc/cvmfs/config.d/neurodesk.ardc.edu.au.conf.*

# Storage volume. World-writable to match neurodesktop's HPC-friendly default
# (UID inside container may not be 1000 under Apptainer).
RUN mkdir -p /data /neurodesktop-storage \
    && chown ${NB_UID}:${NB_GID} /neurodesktop-storage \
    && chmod 0777 /neurodesktop-storage

# Environment bootstrap: builds MODULEPATH from local + CVMFS module roots and
# auto-falls-back when CVMFS is unavailable. Sourced by interactive shells via
# /etc/profile.d/ AND by non-login bash via /etc/bash.bashrc (so `bash -c` and
# jupyter kernel subshells see MODULEPATH too).
COPY environment_variables.sh /etc/profile.d/neurodesk.sh
RUN chmod 0644 /etc/profile.d/neurodesk.sh \
    && printf '\n# neurodesk env (MODULEPATH, APPTAINER_BINDPATH, etc.)\nif [ -f /etc/profile.d/neurodesk.sh ]; then . /etc/profile.d/neurodesk.sh; fi\n' >> /etc/bash.bashrc \
    && printf '\n# Lmod init\nif [ -f /usr/share/lmod/lmod/init/profile ]; then . /usr/share/lmod/lmod/init/profile; fi\n' >> /etc/bash.bashrc

# Cache-bust the neurocommand layer when upstream changes (mirrors neurodesktop's pattern).
ADD https://api.github.com/repos/neurodesk/neurocommand/git/refs/heads/main /tmp/neurocommand-skipcache
RUN rm /tmp/neurocommand-skipcache \
    && git clone https://github.com/neurodesk/neurocommand.git /neurocommand \
    && cd /neurocommand \
    && sed -i 's|CONTAINER_PATH=${PATH_PREFIX}/containers|CONTAINER_PATH=${NEURODESKTOP_LOCAL_CONTAINERS:-${PATH_PREFIX}/containers}|g' neurodesk/fetch_containers.sh \
    && sed -i 's|export CONTAINER_PATH="${_base}"/containers|export CONTAINER_PATH="${NEURODESKTOP_LOCAL_CONTAINERS:-${_base}/containers}"|g' neurodesk/fetch_and_run.sh \
    && sed -i 's|CONTAINER_PATH="${_base}"/containers|CONTAINER_PATH="${NEURODESKTOP_LOCAL_CONTAINERS:-${_base}/containers}"|g' neurodesk/fetch_and_run.sh \
    && bash build.sh --cli --edit \
    && bash install.sh \
    && ln -s /neurodesktop-storage/containers /neurocommand/local/containers

# CVMFS mount runs as a foundation pre-start hook (start.sh executes
# /usr/local/bin/before-notebook.d/*.sh as root before dropping to NB_USER).
COPY before-notebook.d/01-cvmfs-mount.sh /usr/local/bin/before-notebook.d/01-cvmfs-mount.sh
RUN chmod 0755 /usr/local/bin/before-notebook.d/01-cvmfs-mount.sh

VOLUME /neurodesktop-storage
WORKDIR /home/${NB_USER}

# Stay as root so foundation start.sh can run hooks (CVMFS mount needs root)
# before dropping to NB_USER for the user command.
USER root

# Image metadata
ARG NEUROCOMMAND_VERSION="development"
ARG BUILD_DATE="unknown"
ARG VCS_REF="unknown"
ENV NEUROCOMMAND_VERSION=${NEUROCOMMAND_VERSION}
LABEL org.opencontainers.image.title="neurocommand" \
      org.opencontainers.image.description="Command-line tool manager that fetches and runs containers seamlessly." \
      org.opencontainers.image.vendor="Neurodesk" \
      org.opencontainers.image.url="https://www.neurodesk.org" \
      org.opencontainers.image.documentation="https://www.neurodesk.org" \
      org.opencontainers.image.source="https://github.com/neurodesk/neurocommand" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${NEUROCOMMAND_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.base.name="quay.io/jupyter/docker-stacks-foundation:${BASE_IMAGE_TAG}"
