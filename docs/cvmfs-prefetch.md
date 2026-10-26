# Publish the FSL GUI startup bundle

FSL `6.0.7.23` has an experimental CVMFS startup file bundle for the FSL GUI
and FSLeyes. The publisher records files opened by both GUIs with no input
images. Loading `fsl/6.0.7.23` opens `.neurodesk-startup` beside the installed
container. CVMFS then prefetches the recorded files asynchronously in parallel.
Module load does not wait for those transfers to finish.

## Prepare the publisher

Use CVMFS publisher and client version `2.14.1` or later. The publisher needs
Python 3, Apptainer or Singularity, `xvfb-run`, `Xvfb`, `xauth`, and `xwininfo`.
The FSL image must contain `strace`, as the `6.0.7.23` recipe does.
`NEURODESK_CONTAINER_RUNTIME` can select an explicit runtime executable.

On publishers without the X11 tools, including the Rocky Linux 10 publisher,
install the isolated profiling toolkit before running sync:

```bash
sudo bash cvmfs/install_prefetch_tools.sh
```

The installer builds a small Apptainer image from `cvmfs/prefetch_x11.def`,
using a Debian base pinned by digest and packages from the dated Debian snapshot
in the definition. It installs and tests Xvfb, X11 utilities,
and software OpenGL under `/opt/neurodesk-prefetch`. Host packages are unchanged.
The publisher automatically uses this toolkit when native tools are missing.
`NEURODESK_PREFETCH_TOOLS` can select another toolkit prefix. The host still runs
Python and the scientific container runtime; only X11 tools run inside the
toolkit image. Re-running the installer reuses the image for the same definition
and checks OpenGL again. A missing toolkit produces an explicit repair command.

The installed image must be an unpacked directory with `commands.txt`,
`env.txt`, and the generated `fsl`, `fsleyes`, and `container_runtime.sh`
wrappers. The version comes from the published inventory. The publisher uses
the newest build of `fsl/6.0.7.23` listed in `cvmfs/log.txt`; it does not select
an unlisted image or fall back to an older build after an install failure.

## Publish the bundle

Run the usual `cvmfs/sync_containers_to_cvmfs.sh` on the publisher. After
installing containers and reconciling their modules and wrappers, it checks the
startup bundle. If needed, it opens a transaction, records both GUIs, and
publishes the trigger and manifest. Existing installations receive the bundle
too. Wrapper reconciliation runs first so the bundle can include its current
runtime helper.

The recorder runs each GUI on a private virtual display with software OpenGL.
It requires the application window to appear within 45 seconds and records
another two seconds of startup. It then stops that GUI and its traced children.
Successful file opens and executed programs become repository-root paths in
`.cvmfsbundle-.neurodesk-startup`. Container environment scripts, script
interpreters, ELF loaders, and the two host wrappers join the recorded files.
Temporary files and user-home files are excluded. The combined file size must
not exceed 1 GiB. A failed GUI startup aborts publication.

A published profile is reused for its immutable container build. To record
startup again, run these commands from the Neurocommand checkout with exclusive
access to the publisher transaction:

```bash
cvmfs_server transaction neurodesk.ardc.edu.au
python3 cvmfs/prefetch_filebundles.py \
  --repo-root /cvmfs/neurodesk.ardc.edu.au \
  --log cvmfs/log.txt --refresh
cvmfs_server publish neurodesk.ardc.edu.au
```

Publish only after the Python command succeeds. If it fails, run
`cvmfs_server abort -f neurodesk.ardc.edu.au`.

For diagnosis, `--traces DIRECTORY` imports existing `fsl.trace` and
`fsleyes.trace` files recorded inside the image with
`strace -f -qq -yy -s 4096 -e trace=open,openat,openat2,execve`.
Use complete traces from successful GUI startups. This option does not launch
or verify the GUIs. `--check` performs no writes and does not launch a GUI.
Its exit codes are `0` for no pending change, `1` for a missing or stale bundle,
and `2` for an inspection error. A missing latest installation prints a deferral
and exits `0`, leaving retry to the next sync.

## Enable the client before mounting

The Neurocommand Docker image installs
[`docker/cvmfs/neurodesk.ardc.edu.au.local`](../docker/cvmfs/neurodesk.ardc.edu.au.local)
as `/etc/cvmfs/config.d/neurodesk.ardc.edu.au.local`. This repository override
enables `CVMFS_PREFETCH_FILEBUNDLES=yes` without changing the downloaded
Neurodesktop defaults or the generated server ranking.

For other clients, set `CVMFS_PREFETCH_FILEBUNDLES=yes` in
`/etc/cvmfs/default.local` or the repository's `.local` configuration before
mounting. An existing mount needs an unmount and fresh mount to read the option.
A shell export after mounting does not enable it.

## Verify the published trigger

After publication reaches a client, check the trigger on that mount:

```bash
attr -g bundle_trigger \
  /cvmfs/neurodesk.ardc.edu.au/containers/fsl_6.0.7.23_20261013/.neurodesk-startup
module load fsl/6.0.7.23
```

The attribute must report `1`. For a different build date in the inventory,
use that build's directory. Compare FSL GUI and FSLeyes startup with a fresh
cache before and after enabling bundles. Do not remove a cache used by an
active mount. No startup speedup is assumed until it is measured on that client.

Lua and Tcl modulefiles open the trigger only while loading. Help, show,
spider, and unload do not start prefetching. A missing trigger leaves ordinary
module loading available, including on local SIF deployments and older clients.

See the [CVMFS file-bundle documentation](https://cvmfs.readthedocs.io/en/2.14/cpt-file-bundles/)
and [2.14.1 release notes](https://cvmfs.readthedocs.io/en/2.14/cpt-releasenotes/).
