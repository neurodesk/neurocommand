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

Wrappers and modulefiles contain absolute paths. Repeat refresh after each move,
then unload and reload any modules already loaded in your shell. Old module roots
in your shell's `MODULEPATH` need updating with `module unuse` and `module use`.
The refresh command repairs this container's generated files. It does not rewrite
other installation settings in `config.ini`.

A normal fetch of an existing image first checks that the image runs. If the
modulefiles point at another directory, or the Tcl modulefile is missing, the
fetch regenerates the files from the saved inventories. It inspects the image
again only when those inventories are missing. Otherwise it changes nothing. Use
`--refresh` to regenerate without a container runtime. Wrappers regenerated
from inventories use the `--env` interface supported by Singularity 3.6 and
later and Apptainer.
