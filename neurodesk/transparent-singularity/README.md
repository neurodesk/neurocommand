This project allows to use singularity containers transparently on HPCs, so that an application inside the container can be used without adjusting any scripts or pipelines (e.g. nipype). 

_Information on the **Neurodesk** project is available at [neurodesk.github.io](https://neurodesk.github.io)_

_Information on **Transparent Singularity** is available at [neurodesk.github.io/developers/transparent_singularity](https://neurodesk.github.io/developers/architecture/transparent_singularity)_

## Regenerate wrappers and modulefiles offline

Run the generator copied into an installed container directory:

```bash
bash "/new/container root/fsl_6.0.7.18_20250928/run_transparent_singularity.sh" \
  --container fsl_6.0.7.18_20250928.simg --refresh
```

The image, nonempty `commands.txt`, and `env.txt` must already exist beside the
script. Refresh uses `README.md` when present. It regenerates the wrappers,
activation scripts, Lua modulefile, and extensionless Tcl modulefile without
network access or image execution. It rejects `--unpack true`.

For an installed Neurocommand tree, prefer `fetch_containers.sh NAME VERSION DATE
--refresh` so the current helper scripts and manual snippets are copied first.
Both generated modulefiles expose command metadata and `DEPLOY_ENV_` variables.
Manual Lua snippets remain in `manual_module_files/`; Tcl snippets live in
`manual_module_files/tcl/`. Freesurfer and MATLAB have snippets in both formats.

### Runtime policy and portable deployments

Generated wrappers use the adjacent `container_runtime.sh`. Set
`NEURODESK_CONTAINER_RUNTIME` to an executable name or path to choose a runtime.
Otherwise Neurodesk prefers Apptainer, then Singularity. An invalid explicit
choice fails. `NEURODESK_CONTAINER_LOG_LEVEL` accepts `quiet` (default), `normal`,
or `debug` for all runtime operations.

`NEURODESK_GPU=auto` preserves explicitly set `APPTAINER_NV` and `SINGULARITY_NV`,
including zero or empty values. With neither set, an NVIDIA driver enables GPU
support. `on` enables both namespaces; `off` clears them in the child process,
removes legacy `--nv`, and passes `--no-nv`. The parent environment is unchanged.
DISPLAY, a valid XAUTHORITY file, native binds, custom temporary directories and
command arguments remain available. `neurodesk_singularity_opts` retains its
legacy whitespace splitting; embedded shell quoting is not interpreted.

Wrappers resolve their image beside themselves. Lua and Tcl modulefiles derive
paths from their canonical or category publication location. Move or mount the
complete tree, then `module use /new/root/containers/modules`, or use a category
under `/new/root/neurodesk-modules`. `NEURODESK_CVMFS_ROOT` configures external
CVMFS discovery, default `/cvmfs/neurodesk.ardc.edu.au`; local and copied module
trees retain their own root. Loading and running an existing deployment never
regenerates files or downloads an image.

Offline `--refresh` reads saved inventories and requires no runtime. Recognized
historical wrappers migrate; custom, disabled, symlink and non-executable targets
remain intact. Generated modulefiles carry a content checksum. Custom edits are
preserved and reported; refreshed generated metadata can advance without
mistaking a catalog update for an administrator edit. Activation scripts now
modify the current shell only. Persistent activation is an explicit command in
your own shell startup file, for example `source /new/path/activate_IMAGE.sh`.
