Neurocommand is a command line interface for advanced users.


_Information on the **Neurodesk** project is available at [neurodesk.org](https://neurodesk.org)_

_Information on **Neurocommand** is available at [neurodesk.org/docs/neurocommand](https://neurodesk.org/docs/neurocommand)_

## Find the container that provides a command

Search executable names with:

```bash
ml keyword bet
```

Neurocommand includes each container's exposed commands in its `whatis` metadata.
Lmod's [keyword search](https://lmod.readthedocs.io/en/latest/010_user.html)
returns matching modules, such as `fsl/6.0.7.18`, which you can load directly:

```bash
ml fsl/6.0.7.18
```

Use a module version returned by your search. Keyword searches also match module
help and other `whatis` descriptions, so results can include other matches.
`ml av bet` searches module names and does not search this command metadata.

Discovery metadata excludes hidden files and shared-library names ending in
`.so`, `.so.*`, `.dylib`, or `.dll`, even when the inventory marks them executable.
The CVMFS sync replaces previously generated command extensions with `whatis`
metadata in existing modulefiles without rebuilding container images.
For versions outside the active container list, sync removes obsolete generated
extension blocks while preserving the modulefiles and other metadata.
Command inventories and wrappers are preserved.

## Use your host module system

Neurocommand generates Lua modulefiles for Lmod and extensionless Tcl modulefiles
for Environment Modules. Initialize your host's `module` command before using
`fetch_and_run.sh`. Container commands require Apptainer or Singularity.
On CVMFS, module reconciliation generates the Tcl modulefile for each kept
container that has only a Lua modulefile.
Reconciliation accepts existing inventories with no usable commands and omits
their command metadata. Installation, refresh, and artifact checks still require
a nonempty command inventory.
When an existing legacy module references an inventory that fails validation,
reconciliation preserves that module because its generated ownership is unknown.
Protected canonical content is not copied into public module paths. Modules
generated directly from a valid selected inventory remain eligible for publication.

For Environment Modules, search command metadata with `module keyword bet`,
then load a result with `module load fsl/6.0.7.18`.

## Refresh an installed container after moving Neurocommand

Run the current installation's fetch script to rebuild wrappers and modulefiles
from its saved inventories:

```bash
bash /new/location/local/fetch_containers.sh fsl 6.0.7.18 20250928 --refresh
```

If you store containers separately, set their current root explicitly:

```bash
NEURODESKTOP_LOCAL_CONTAINERS="/new/container root" \
  bash /new/location/local/fetch_containers.sh fsl 6.0.7.18 20250928 --refresh
```

Refresh requires the installed `.simg` image, `commands.txt`, and `env.txt` in the
dated container directory. The image can be a file, sandbox directory, or valid
symlink. `README.md` is optional. Refresh copies the current helpers and manual
module snippets, then regenerates wrappers, activation scripts, and both module
formats. It does not download, execute, or unpack the image and works without a
container runtime installed. Missing inputs cause an error before any files change.

Current wrappers and modulefiles derive their paths from their deployment location.
Refresh older installations once to migrate their generated files, then unload and
reload modules already loaded in your shell. Old module roots
in your shell's `MODULEPATH` need updating with `module unuse` and `module use`.
The refresh command repairs this container's generated files. It does not rewrite
other installation settings in `config.ini`.

A normal fetch of an existing image leaves current generated artifacts unchanged.
Older artifacts or missing module formats are regenerated from saved inventories. It inspects the image
again only when those inventories are missing. Otherwise it changes nothing. Use
`--refresh` to regenerate without a container runtime. Wrappers regenerated
from inventories use the `--env` interface supported by Singularity 3.6 and
later and Apptainer.

### Versioned tool bundles and shells

`bidstools/2026.10` loads the independent `bidscoin/4.6.2` and
`dcm2niix/v1.0.20260724` modules. Older bidstools container versions remain
available. This bundle omits bru2nii because no standalone published image exists.
Bundles have no container image and never run a combined container.

```bash
bash local/fetch_and_run.sh bidstools 2026.10
# In the host shell: ml another-tool/version
bash local/fetch_and_run.sh bidstools 2026.10 dcm2niix --help
bash local/fetch_and_run.sh bidscoin 4.6.2 --container-shell
```

The default shell is a host Bash shell with the selected module loaded, a prompt
showing its version, and the normal user Bash startup file read once. Use
`--host-shell` or `--container-shell`, or set `NEURODESK_SHELL_MODE=host|container`.
Isolated shells require a single container module; bundles explain this restriction
before fetching anything. Named commands preserve their arguments and selected
module image identity even when other tools precede them on PATH.

Bundle dependency lifetime is managed by the module engine. Dependencies loaded
before a bundle remain loaded when it unloads; shared dependencies remain until
their last owner unloads. Lmod with `depends_on` support also retains a dependency explicitly
loaded after the bundle. Environment Modules 5.0 treats loading an already-loaded
dependency as a no-op: preload dependencies you want to retain independently.
Environment Modules requires automatic dependency handling. Older Lmod supports
single container modules but reports an actionable error for bundles.

For newly loaded dependencies, the last listed dependency takes PATH precedence.
Already-loaded dependencies retain the engine's existing order. Host startup
restores only active Neurodesk wrapper paths after user startup changes PATH;
it preserves unrelated startup paths and native dependency ownership.

`neurodesk/bundles.json` declares versioned identities, ordered exact dependency
versions, descriptions and categories. Dependencies must exist in `apps.json`;
bundle dependencies, duplicate tools and unsafe names are rejected. Menu creation
and CVMFS maintenance publish the same Lua and Tcl bundle modules. Maintenance
skips unavailable dependencies, preserves customized files and publishes only when
generated bundle contents change.

Bundle lifetime and host startup were tested with Lmod 8.7.54 and Environment
Modules 5.0.1.
