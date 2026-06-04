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
        squashfs-tools fuse-overlayfs squashfuse \
        sudo \
        bc dnsutils git graphviz less nano openjdk-21-jre-headless openssh-client rsync tcsh tree unzip vim xz-utils zip \
        libgl1-mesa-dri libglx-mesa0 libosmesa6 libegl1 libegl-mesa0 libglu1-mesa \
    && apt-get install --yes --no-install-recommends \
        autofs cvmfs uuid-dev \
        lmod lua5.2 lua-bit32 lua-filesystem lua-json lua-lpeg lua-posix lua-term \
        tcllib tk \
        squashfs-tools fuse-overlayfs squashfuse \
        sudo \
        bc dnsutils git graphviz less nano openjdk-21-jre-headless openssh-client rsync tcsh tree unzip vim xz-utils zip \
        libgl1-mesa-dri libglx-mesa0 libosmesa6 libegl1 libegl-mesa0 libglu1-mesa \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /root/.cache

# Nextflow (JVM workflow tool; JRE comes from openjdk above). Notebooks expect
# the binary on PATH — they don't module-load it. Matches neurodesktop.
RUN curl -fsSL https://get.nextflow.io | bash \
    && mv nextflow /usr/local/bin/nextflow \
    && chmod 0755 /usr/local/bin/nextflow

# Install apptainer from its official multi-arch image. This avoids the
# Launchpad API and PPA apt fetches, which have been flaky in scheduled builds.
COPY --from=apptainer /opt/apptainer /opt/apptainer
RUN ln -sf /opt/apptainer/bin/apptainer /usr/local/bin/apptainer \
    && ln -sf /opt/apptainer/bin/singularity /usr/local/bin/singularity \
    && rm -rf /opt/apptainer/libexec/apptainer/cni \
    && sed -i 's/^allow setuid = yes/allow setuid = no/' /opt/apptainer/etc/apptainer/apptainer.conf

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
    && mkdir -p /opt/neurodesktop \
    && ln -sf /etc/profile.d/neurodesk.sh /opt/neurodesktop/environment_variables.sh \
    && printf '\n# neurodesk env (MODULEPATH, APPTAINER_BINDPATH, etc.)\nif [ -f /etc/profile.d/neurodesk.sh ]; then . /etc/profile.d/neurodesk.sh; fi\n' >> /etc/bash.bashrc \
    && printf '\n# Lmod init\nif [ -f /usr/share/lmod/lmod/init/profile ]; then . /usr/share/lmod/lmod/init/profile; fi\n' >> /etc/bash.bashrc

# Pin APPTAINER_BINDPATH as a static ENV (not just the sourced env file) so it
# reaches the jupyter kernel and its apptainer-exec subprocesses even when
# /etc/bash.bashrc isn't sourced. Without this, module-loaded tools run inside
# apptainer containers without /cvmfs bound (notebooks that resolve .simg-internal
# template paths then fail). Mirrors how neurodesktop's kernel wrapper guarantees it.
ENV APPTAINER_BINDPATH=/data,/mnt,/neurodesktop-storage,/tmp,/cvmfs

# Cache-bust the neurocommand layer when upstream changes (mirrors neurodesktop's pattern).
ADD https://api.github.com/repos/neurodesk/neurocommand/git/refs/heads/main /tmp/neurocommand-skipcache
RUN rm /tmp/neurocommand-skipcache \
    && git clone https://github.com/neurodesk/neurocommand.git /neurocommand \
    && cd /neurocommand \
    && bash build.sh --cli --edit \
    && bash install.sh \
    && ln -s /neurodesktop-storage/containers /neurocommand/local/containers

# Python — notebook execution / CI.
RUN pip install --no-cache-dir \
        nbconvert \
        nbclient \
        ipykernel \
        ipywidgets \
        'huggingface_hub>=0.32.0' \
        jupyterlmod \
        watermark \
        papermill

# Python — scientific stack (imported in cell 1 of most notebooks).
RUN pip install --no-cache-dir \
        numpy \
        scipy \
        pandas \
        matplotlib \
        seaborn \
        Pillow \
        beautifulsoup4 \
        ipython \
        nest_asyncio

# Python — neuroimaging. build-essential is installed and purged in the same
# layer so transitive deps without an aarch64 wheel (e.g. traits via nipype)
# can compile from source without leaving the toolchain in the final image.
RUN apt-get update --yes \
    && apt-get install --yes --no-install-recommends build-essential \
    && pip install --no-cache-dir \
        nibabel \
        nilearn \
        nipype \
        pydicom \
        dipy \
        mne \
        pydra \
        niwrap \
    && apt-get purge --yes --auto-remove build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /root/.cache

# CLI tools notebooks invoke via shell escape (osf, datalad, etc.).
RUN pip install --no-cache-dir \
        datalad \
        datalad-container \
        datalad-osf \
        osfclient

# Python — visualization (used by ~43% of notebooks).
RUN pip install --no-cache-dir ipyniivue

# Restore jovyan ownership of the conda env and home dir after the
# root-level pip installs above, so jupyter kernel.json (and any other
# state under $HOME like datalad's cache or pip --user installs) remain
# writable by jovyan at runtime. fix-permissions alone only chgrps; we
# need a full chown to handle root-owned files from earlier layers.
RUN chown -R ${NB_UID}:${NB_GID} /opt/conda /home/${NB_USER} \
    && install -d -o ${NB_UID} -g ${NB_GID} -m 0755 \
        /home/${NB_USER}/.local \
        /home/${NB_USER}/.cache \
    && if [ -x /usr/local/bin/fix-permissions ]; then \
        fix-permissions /opt/conda && fix-permissions /home/${NB_USER}; \
    fi

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
