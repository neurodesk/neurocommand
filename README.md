Neurocommand is a command line interface for advanced users.


_Information on the **Neurodesk** project is available at [neurodesk.org](https://neurodesk.org)_

_Information on **Neurocommand** is available at [neurodesk.org/docs/neurocommand](https://neurodesk.org/docs/neurocommand)_

## Find the container that provides a command

Neurocommand publishes each container's exposed commands as [Lmod extensions](https://lmod.readthedocs.io/en/latest/330_extensions.html). Search for a command across all available containers with:

```bash
module spider bet
```

The first search lists the available extension versions. Ask for one exact version to see the module that provides it:

```bash
module spider bet/6.0.7.18
```

The result identifies the container module to load, for example `fsl/6.0.7.18`. The extension version is the providing container's version; it does not necessarily report the executable's own internal version.
