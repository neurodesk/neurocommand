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
