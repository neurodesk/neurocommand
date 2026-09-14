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
