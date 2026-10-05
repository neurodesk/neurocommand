#!/usr/bin/env python3
"""Render relocatable deployment artifacts from saved container inventories."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
import hashlib
from pathlib import Path
import re
import shlex
import tempfile
import os
import sys


@dataclass(frozen=True)
class ModuleId:
    name: str
    version: str


@dataclass(frozen=True)
class ContainerSpec:
    module: ModuleId
    image_basename: str
    commands: tuple[str, ...]
    deploy_env: tuple[tuple[str, str], ...]
    help_text: str
    manual_lua: str = ''
    manual_tcl: str = ''


@dataclass(frozen=True)
class BundleSpec:
    module: ModuleId
    dependencies: tuple[ModuleId, ...]
    categories: tuple[str, ...]
    description: str

LUA_ROOT = '''local filename = myFileName()
local module_dir = filename:match("^(.*)/[^/]+$")
local canonical = module_dir:match("^(.*)/modules/[^/]+$")
local published = module_dir:match("^(.*)/neurodesk%-modules/[^/]+/[^/]+$")
local root = canonical or (published and pathJoin(published, "containers"))
if not root then root = pathJoin(os.getenv("NEURODESK_CVMFS_ROOT") or "/cvmfs/neurodesk.ardc.edu.au", "containers") end'''

TCL_ROOT = '''set module_dir [file dirname $ModulesCurrentModulefile]
if {[file tail [file dirname $module_dir]] eq "modules"} {
    set root [file dirname [file dirname $module_dir]]
} elseif {[file tail [file dirname [file dirname $module_dir]]] eq "neurodesk-modules"} {
    set root [file join [file dirname [file dirname [file dirname $module_dir]]] containers]
} else {
    set repository /cvmfs/neurodesk.ardc.edu.au
    if {[info exists env(NEURODESK_CVMFS_ROOT)]} {set repository $env(NEURODESK_CVMFS_ROOT)}
    set root [file join $repository containers]
}'''



def identity_variable(module: ModuleId) -> str:
    return 'NEURODESK_IMAGE_' + f'{module.name}/{module.version}'.encode().hex().upper()


def safe_command(command: str) -> bool:
    return bool(command) and command not in {'.', '..'} and not re.search(r'[/\\\s\x00]', command)


def read_container_inventory(directory: Path, image_basename: str | None = None) -> ContainerSpec:
    image_basename = image_basename or directory.name + '.simg'
    name_version, date = image_basename.removesuffix('.simg').rsplit('_', 1)
    name, version = name_version.rsplit('_', 1)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', name) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', version):
        raise ValueError('Unsafe container name or version')
    if not re.fullmatch(r'\d{8}', date):
        raise ValueError('Container identity requires an eight digit build date')
    commands = tuple(dict.fromkeys(c for c in (directory / 'commands.txt').read_text().splitlines() if safe_command(c)))
    if not commands:
        raise ValueError('Missing or empty commands.txt')
    environment = []
    with (directory / 'env.txt').open(newline='') as inventory:
        records = inventory.read().split('\n')
    for line in records:
        if line.startswith('DEPLOY_ENV_') and '=' in line:
            key, value = line.split('=', 1)
            key = key.removeprefix('DEPLOY_ENV_')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                raise ValueError(f'Invalid environment name: {key}')
            environment.append((key, value))
    def read(path: Path) -> str:
        if not path.is_file():
            return ''
        with path.open(errors='replace', newline='') as source:
            return source.read().rstrip('\n')
    return ContainerSpec(ModuleId(name, version), image_basename, commands,
                         tuple(environment), read(directory / 'README.md'),
                         read(directory / 'manual_module_files' / name),
                         read(directory / 'manual_module_files/tcl' / name))


def lua(value: str) -> str:
    escaped = []
    for character in value:
        if character in ('"', '\\'):
            escaped.append('\\' + character)
        elif ord(character) < 32 or ord(character) == 127:
            escaped.append(f'\\{ord(character):03d}')
        else:
            escaped.append(character)
    return '"' + ''.join(escaped) + '"'


def tcl(value: str) -> str:
    for raw, escaped in [('\\', '\\\\'), ('"', '\\"'), ('$', '\\$'), ('[', '\\['), (']', '\\]'), ('{', '\\{'), ('}', '\\}'), ('\n', '\\n'), ('\r', '\\r')]:
        value = value.replace(raw, escaped)
    return '"' + value + '"'


def render_wrapper(image_basename: str, command: str, *, legacy: bool = False) -> bytes:
    prefix = 'NEURODESK_CONTAINER_LEGACY_ENV=1 ' if legacy else ''
    return ('''#!/usr/bin/env bash
# neurodesk-artifact-v2
_neurodesk_wrapper=$(readlink -f -- "${BASH_SOURCE[0]}") || exit 2
_neurodesk_dir=$(cd "$(dirname "$_neurodesk_wrapper")" && pwd -P) || exit 2
source "$_neurodesk_dir/container_runtime.sh" || exit 2
''' + prefix + 'neurodesk_container exec "$_neurodesk_dir/"' + shlex.quote(image_basename) + ' ' + shlex.quote(command) + ' "$@"\n').encode()


def stamp_artifact(content: bytes, *, format: str) -> bytes:
    comment = "--" if format == "lua" else "#"
    digest = hashlib.sha256(content).hexdigest()
    header, body = content.split(b"\n", 1)
    return header + b"\n" + f"{comment} neurodesk-artifact-sha256 {digest}\n".encode() + body


def owns_artifact(content: str) -> bool:
    marker = re.search(r"(?m)^(?:--|#) neurodesk-artifact-sha256 ([a-f0-9]{64})\n", content)
    return bool(marker and hashlib.sha256((content[:marker.start()] + content[marker.end():]).encode()).hexdigest() == marker[1])


def render_module(spec: ContainerSpec | BundleSpec, *, format: str) -> bytes:
    if isinstance(spec, BundleSpec):
        return render_bundle(spec, format=format)
    image_dir = spec.image_basename.removesuffix('.simg')
    commands = sorted(c for c in spec.commands if not c.startswith('.') and not re.search(r'\.(so(?:\..*)?|dll|dylib)$', c, re.I))
    if format == 'lua':
        lines = ['-- -*- lua -*-', '-- neurodesk-artifact-v2', f'-- neurodesk-image {spec.image_basename}', f'help({lua(spec.help_text.replace("]]", "] ]"))})', f'whatis({lua(spec.image_basename)})', LUA_ROOT, f'local container_dir = pathJoin(root, {lua(image_dir)})', f'local image = pathJoin(container_dir, {lua(spec.image_basename)})', 'if mode() == "load" and not isDir(image) and not isFile(image) then LmodError("Missing Neurodesk image: " .. image) end', 'prepend_path("PATH", container_dir)', f'setenv({lua(identity_variable(spec.module))}, image)']
        if commands:
            lines += ['-- neurodesk-exposed-commands', f'whatis({lua("Commands: " + ", ".join(commands))})']
        for key, value in spec.deploy_env:
            parts = value.split('BASEPATH')
            expression = ' .. image .. '.join(lua(part) for part in parts)
            lines.append(f'setenv({lua(key)}, {expression})')
        snippet, marker = spec.manual_lua, '--'
    else:
        lines = ['#%Module1.0', '# neurodesk-artifact-v2', f'# neurodesk-image {spec.image_basename}', f'proc ModulesHelp {{ }} {{ puts stderr {tcl(spec.help_text)} }}', f'module-whatis {tcl(spec.image_basename)}', TCL_ROOT, f'set container_dir [file join $root {tcl(image_dir)}]', f'set image [file join $container_dir {tcl(spec.image_basename)}]', 'if {[module-info mode load] && ![file exists $image]} {error "Missing Neurodesk image: $image"}', 'prepend-path PATH $container_dir', f'setenv {tcl(identity_variable(spec.module))} $image']
        if commands:
            lines += ['# neurodesk-exposed-commands', f'module-whatis {tcl("Commands: " + ", ".join(commands))}']
        for key, value in spec.deploy_env:
            lines.append(f'setenv {tcl(key)} [string map [list BASEPATH $image] {tcl(value)}]')
        snippet, marker = spec.manual_tcl, '#'
    if snippet:
        lines += [f'{marker} neurodesk-manual-module-begin', snippet.replace('toolVersion', spec.module.version).rstrip('\n'), f'{marker} neurodesk-manual-module-end']
    content = ('\n'.join(lines) + '\n').encode()
    return stamp_artifact(content, format=format)


def managed_module_content(content: str, spec: ContainerSpec, *, format: str, containers_root: Path | None = None) -> bytes | None:
    """Only replace complete recognized generated structure, preserving edits."""
    rendered = render_module(spec, format=format)
    if content.encode() == rendered:
        return rendered
    if 'neurodesk-artifact-v2' in content:
        return rendered if owns_artifact(content) else None
    pattern = r'prepend_path\("PATH", ("(?:\\.|[^"\\])*")\)' if format == 'lua' else r'(?m)^prepend-path PATH ("(?:\\.|[^"\\])*")$'
    paths = re.findall(pattern, content)
    if len(paths) != 1:
        return None
    if format == 'lua':
        try:
            directory = Path(json.loads(paths[0]))
        except ValueError:
            return None
    else:
        directory = Path(re.sub(r'\\(.)', lambda m: {'n': '\n', 'r': '\r'}.get(m[1], m[1]), paths[0][1:-1]))
    old_image = directory.name + '.simg'
    old_spec = replace(spec, image_basename=old_image)
    if containers_root is not None:
        old_directory = containers_root / directory.name
        if (old_directory / 'env.txt').is_file() and (old_directory / 'commands.txt').is_file():
            try:
                old_spec = read_container_inventory(old_directory)
            except ValueError:
                return None
    # Exact historical output is the only migration authority. Inventory env
    # and owned manual snippets must agree too, so site additions survive.
    if legacy_module_content(old_spec, directory, format=format) != content:
        return None
    return rendered


def legacy_module_content(spec: ContainerSpec, directory: Path, *, format: str) -> str:
    def historical_lua(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    commands = sorted(c for c in spec.commands if not c.startswith('.') and not re.search(r'\.(so(?:\..*)?|dll|dylib)$', c, re.I))
    if format == 'lua':
        lines = ['-- -*- lua -*-', 'help([===[', spec.help_text.replace(']]', '] ]'), ']===])', f'whatis({historical_lua(spec.image_basename)})']
        if commands:
            lines += ['-- neurodesk-exposed-commands', f'whatis({historical_lua("Commands: " + ", ".join(commands))})']
        lines += [f'prepend_path("PATH", {historical_lua(str(directory))})']
        for key, value in spec.deploy_env:
            lines += [f'setenv({historical_lua(key)}, {historical_lua(value.replace("BASEPATH", str(directory / spec.image_basename)))})']
        snippet, marker = spec.manual_lua, '--'
    else:
        lines = ['#%Module1.0', f'proc ModulesHelp {{ }} {{ puts stderr {tcl(spec.help_text)} }}', f'module-whatis {tcl(spec.image_basename)}']
        if commands:
            lines += ['# neurodesk-exposed-commands', f'module-whatis {tcl("Commands: " + ", ".join(commands))}']
        lines += [f'prepend-path PATH {tcl(str(directory))}']
        for key, value in spec.deploy_env:
            lines += [f'setenv {tcl(key)} {tcl(value.replace("BASEPATH", str(directory / spec.image_basename)))}']
        snippet, marker = spec.manual_tcl, '#'
    if snippet:
        lines += [f'{marker} neurodesk-manual-module-begin', snippet.replace('toolVersion', spec.module.version).rstrip('\n'), f'{marker} neurodesk-manual-module-end']
    return '\n'.join(lines) + '\n'


def render_activation(image_basename: str, activate: bool) -> bytes:
    text = '''# neurodesk-artifact-v2
_neurodesk_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
'''
    if activate:
        text += 'export PATH="$_neurodesk_dir:$PATH"\n'
    else:
        text += '''_neurodesk_path=()
IFS=: read -r -a _neurodesk_parts <<< "$PATH"
for _neurodesk_part in "${_neurodesk_parts[@]}"; do
    [[ $_neurodesk_part == "$_neurodesk_dir" ]] || _neurodesk_path+=("$_neurodesk_part")
done
PATH=$(IFS=:; echo "${_neurodesk_path[*]}")
export PATH
unset _neurodesk_path _neurodesk_parts _neurodesk_part
'''
    return (text + 'unset _neurodesk_dir\n').encode()


def write_artifact(path: Path, content: bytes, executable: bool = False) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink: {path}")
    if path.is_file() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.stat() if path.exists() else None
    mode = before.st_mode & 0o7777 if before is not None else (0o755 if executable else 0o644)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary_name = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            owner = os.fstat(temporary.fileno())
            if before is not None and (owner.st_uid, owner.st_gid) != (before.st_uid, before.st_gid):
                os.fchown(temporary.fileno(), before.st_uid, before.st_gid)
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            temporary_name.unlink(missing_ok=True)


def catalog_builddate(catalog: dict, module: ModuleId) -> str:
    title = f'{module.name} {module.version}'
    for group in catalog.values():
        if isinstance(group, dict):
            record = group.get('apps', {}).get(title)
            if isinstance(record, dict):
                return str(record.get('version', ''))
    return ''


def load_bundles(path: Path, catalog: dict) -> tuple[BundleSpec, ...]:
    if not isinstance(catalog, dict):
        raise ValueError('Container catalog must be an object')
    for name, group in catalog.items():
        if isinstance(group, dict) and not isinstance(group.get('apps', {}), dict):
            raise ValueError(f'Container catalog group {name} apps must be an object')
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'bundles'} or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('Bundle manifest requires schema_version 1 and bundles')
    if not isinstance(manifest['bundles'], list):
        raise ValueError('bundles must be a list')
    def module_id(record: dict) -> ModuleId:
        if not isinstance(record, dict) or set(record) != {'name', 'version'}:
            raise ValueError('Module identity requires name and version')
        if any(not isinstance(record[k], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', record[k]) for k in ['name', 'version']):
            raise ValueError('Invalid bundle module identity')
        return ModuleId(record['name'], record['version'])
    result = []
    identities = set()
    for record in manifest['bundles']:
        if not isinstance(record, dict) or set(record) != {'name', 'version', 'description', 'categories', 'dependencies'}:
            raise ValueError('Invalid bundle fields')
        module = module_id({k: record[k] for k in ['name', 'version']})
        if module in identities or catalog_builddate(catalog, module):
            raise ValueError(f'Duplicate bundle/container identity: {module.name}/{module.version}')
        identities.add(module)
        if not isinstance(record['dependencies'], list) or not record['dependencies']:
            raise ValueError('Bundle requires at least one dependency')
        dependencies = tuple(module_id(dep) for dep in record['dependencies'])
        if len(set(dependencies)) != len(dependencies) or len({dep.name for dep in dependencies}) != len(dependencies):
            raise ValueError('Duplicate or incompatible dependency versions')
        for dep in dependencies:
            if not re.fullmatch(r'[0-9]{8}', catalog_builddate(catalog, dep)):
                raise ValueError(f'Unknown container dependency: {dep.name}/{dep.version}')
        categories = record['categories']
        if not isinstance(categories, list) or not categories or any(not isinstance(c, str) or not c.strip() or '/' in c or '\\' in c or '\x00' in c or c.strip() in {'.', '..'} for c in categories) or len(set(categories)) != len(categories):
            raise ValueError('Invalid bundle categories')
        if not isinstance(record['description'], str):
            raise ValueError('Bundle description must be text')
        result.append(BundleSpec(module, dependencies, tuple(categories), record['description']))
    return tuple(result)


LUA_DEPENDENCY_PREFLIGHT = '''local function neurodesk_check_dependency(name, version)
    local roots = {}
    for entry in (os.getenv("MODULEPATH") or ""):gmatch("[^:]+") do table.insert(roots, entry) end
    table.insert(roots, pathJoin(root, "modules"))
    for _, module_root in ipairs(roots) do
        for _, suffix in ipairs({".lua", ""}) do
            local filename = pathJoin(module_root, name, version .. suffix)
            local source = io.open(filename, "r")
            if source then
                local content = source:read("*a")
                source:close()
                local basename = content:match('[^\\n]*neurodesk%-image ([%w_.+%-]+%.simg)')
                if not basename then LmodError("Refresh dependency module " .. name .. "/" .. version .. " to generate portable image identity.") end
                local directory = basename:sub(1, -6)
                local canonical = module_root:match("^(.*)/modules$")
                local published = module_root:match("^(.*)/neurodesk%-modules/[^/]+$")
                local containers = canonical or (published and pathJoin(published, "containers")) or root
                local image = pathJoin(containers, directory, basename)
                if not isFile(image) and not isDir(image) then LmodError("Missing Neurodesk dependency image: " .. image) end
                return
            end
        end
    end
    LmodError("Missing dependency " .. name .. "/" .. version .. "; install it with fetch_and_run.sh.")
end'''

TCL_DEPENDENCY_PREFLIGHT = '''proc neurodesk_check_dependency {name version root} {
    global env ModuleTool
    set roots [list]
    if {[info exists env(MODULEPATH)]} {set roots [split $env(MODULEPATH) :]}
    lappend roots [file join $root modules]
    set suffixes [list ""]
    if {[info exists ModuleTool] && $ModuleTool eq "Lmod"} {set suffixes [list .lua ""]}
    foreach module_root $roots {
        foreach suffix $suffixes {
            set filename [file join $module_root $name ${version}${suffix}]
            if {![file isfile $filename]} {continue}
            set channel [open $filename r]
            set content [read $channel]
            close $channel
            set basename ""
            regexp {neurodesk-image ([A-Za-z0-9_.+-]+[.]simg)} $content ignored basename
            if {$basename eq ""} {error "Refresh dependency module $name/$version to generate portable image identity."}
            set directory [string range $basename 0 end-5]
            set containers $root
            if {[file tail $module_root] eq "modules"} {set containers [file dirname $module_root]
            } elseif {[file tail [file dirname $module_root]] eq "neurodesk-modules"} {set containers [file join [file dirname [file dirname $module_root]] containers]}
            set image [file join $containers $directory $basename]
            if {![file exists $image]} {error "Missing Neurodesk dependency image: $image"}
            return
        }
    }
    error "Missing dependency $name/$version; install it with fetch_and_run.sh."
}'''



def render_bundle(spec: BundleSpec, *, format: str) -> bytes:
    dependencies = [f'{dep.name}/{dep.version}' for dep in spec.dependencies]
    help_text = spec.description + '\nDependencies: ' + ', '.join(dependencies) + '\nNew dependencies prepend in listed order; the last listed wins command collisions. Already loaded tools retain the module engine PATH order.'
    if format == 'lua':
        lines = ['-- -*- lua -*-', '-- neurodesk-bundle-v1', f'help({lua(help_text)})', f'whatis({lua("Bundle: " + spec.module.name + "/" + spec.module.version)})', LUA_ROOT, LUA_DEPENDENCY_PREFLIGHT, '''if mode() == "load" then
    if type(depends_on) ~= "function" then
        LmodError("Neurodesk bundles require Lmod with depends_on support. Upgrade Lmod, or load the dependency modules individually.")
    end''']
        for dep, identity in zip(spec.dependencies, dependencies):
            lines += [f'    if isloaded({lua(dep.name)}) and not isloaded({lua(identity)}) then LmodError({lua("Incompatible loaded version of " + dep.name + "; unload it before loading this bundle.")}) end',
                      f'    neurodesk_check_dependency({lua(dep.name)}, {lua(dep.version)})']
        lines += ['end', 'append_path("MODULEPATH", pathJoin(root, "modules"))']
        lines += [f'if type(depends_on) == "function" then depends_on({lua(dep)}) end' for dep in dependencies]
    else:
        lines = ['#%Module1.0', '# neurodesk-bundle-v1', f'proc ModulesHelp {{ }} {{ puts stderr {tcl(help_text)} }}', f'module-whatis {tcl("Bundle: " + spec.module.name + "/" + spec.module.version)}', TCL_ROOT, TCL_DEPENDENCY_PREFLIGHT, '''if {[module-info mode load]} {
    if {![llength [info commands depends-on]] && (![info exists ModuleTool] || $ModuleTool ne "Modules")} {
        error "Neurodesk bundles require Lmod with depends_on support. Upgrade Lmod, or load dependencies individually."
    }
    if {[info exists env(MODULES_AUTO_HANDLING)] && $env(MODULES_AUTO_HANDLING) eq "0"} {
        error "Neurodesk bundles require Environment Modules auto_handling enabled."
    }''']
        for dep, identity in zip(spec.dependencies, dependencies):
            lines += [f'    if {{[is-loaded {tcl(dep.name)}] && ![is-loaded {tcl(identity)}]}} {{error {tcl("Incompatible loaded version of " + dep.name + "; unload it before loading this bundle.")}}}',
                      f'    neurodesk_check_dependency {tcl(dep.name)} {tcl(dep.version)} $root']
        lines += ['}', 'append-path MODULEPATH [file join $root modules]']
        lines += [f'if {{[llength [info commands depends-on]]}} {{depends-on {tcl(dep)}}} else {{prereq {tcl(dep)}}}' for dep in dependencies]
    return stamp_artifact(('\n'.join(lines) + '\n').encode(), format=format)


def bundle_menu_entries(bundles: tuple[BundleSpec, ...]) -> dict:
    result = {}
    for bundle in bundles:
        group = result.setdefault(bundle.module.name, {'categories': [], 'apps': {}})
        group['apps'][f'{bundle.module.name} {bundle.module.version}'] = {'exec': '', 'terminal': True}
        for category in bundle.categories:
            if category not in group['categories']:
                group['categories'].append(category)
    return result


def publish_bundles(bundles: tuple[BundleSpec, ...], module_root: Path, public_root: Path | None = None, *, require_installed: bool = False, check: bool = False) -> bool:
    drift = False
    for bundle in bundles:
        if require_installed:
            missing = [dep for dep in bundle.dependencies if not any((module_root / dep.name / (dep.version + suffix)).is_file() for suffix in ['', '.lua'])]
            if missing:
                print('[WARN] Skipping bundle with missing dependencies: ' + ', '.join(f'{d.name}/{d.version}' for d in missing), file=sys.stderr)
                continue
        for format, suffix in [('lua', '.lua'), ('tcl', '')]:
            content = render_module(bundle, format=format)
            relative = Path(bundle.module.name) / (bundle.module.version + suffix)
            targets = [module_root / relative]
            if public_root is not None:
                targets.extend(public_root / category.strip().replace(' ', '_') / relative for category in bundle.categories)
            for target in targets:
                if target.is_symlink() or (target.exists() and target.read_bytes() != content and not owns_artifact(target.read_text())):
                    print(f"[WARN] Preserving customized bundle module: {target}", file=sys.stderr)
                    continue
                if not target.exists() or target.read_bytes() != content:
                    drift = True
                    if not check:
                        write_artifact(target, content)
    return drift



def bundle_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--module-root', type=Path)
    parser.add_argument('--public-root', type=Path)
    parser.add_argument('--require-installed', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--resolve', nargs=2)
    args = parser.parse_args(argv)
    try:
        catalog = json.loads(args.catalog.read_text())
        bundles = load_bundles(args.manifest, catalog)
        if args.resolve:
            module = ModuleId(*args.resolve)
            selected = next((b for b in bundles if b.module == module), None)
            print('bundle' if selected else 'container')
            for dep in selected.dependencies if selected else [module]:
                print('\t'.join([dep.name, dep.version, catalog_builddate(catalog, dep)]))
        elif args.module_root is not None:
            drift = publish_bundles(bundles, args.module_root, args.public_root, require_installed=args.require_installed, check=args.check)
            if args.check:
                raise SystemExit(1 if drift else 0)
        else:
            parser.error('Supply --resolve or --module-root')
    except (OSError, ValueError) as error:
        print(f"[ERROR] Could not process bundle metadata or publish modules: {error}", file=sys.stderr)
        raise SystemExit(2) from None




def check_container(directory: Path) -> bool:
    """Check managed artifacts without regenerating or touching the image."""
    spec = read_container_inventory(directory)
    source = Path(__file__).resolve().parent
    for name in ['container_runtime.sh', 'artifact_renderer.py', 'wrapper_legacy.py', 'ts_render_artifacts.sh', 'run_transparent_singularity.sh']:
        deployed = directory / name
        if not deployed.is_file() or deployed.is_symlink() or deployed.read_bytes() != (source / name).read_bytes():
            return False
    from wrapper_legacy import classify_relocated_wrapper, WrapperState
    for command in spec.commands:
        wrapper = directory / command
        if not wrapper.exists():
            return False
        if wrapper.is_symlink() or not wrapper.stat().st_mode & 0o111:
            continue
        current = wrapper.read_bytes()
        if current in [render_wrapper(spec.image_basename, command), render_wrapper(spec.image_basename, command, legacy=True)]:
            continue
        state, _ = classify_relocated_wrapper(directory, command, current)
        if state in {WrapperState.LEGACY, WrapperState.FIXED}:
            return False
    for format, suffix in [('lua', '.lua'), ('tcl', '')]:
        module = directory.parent / 'modules' / spec.module.name / (spec.module.version + suffix)
        if not module.is_file():
            return False
        current = module.read_text()
        expected = managed_module_content(current, spec, format=format, containers_root=directory.parent)
        if expected is not None and current.encode() != expected:
            return False
    return True


def main() -> None:
    if sys.argv[1:2] == ['--check-container']:
        try:
            sys.exit(0 if check_container(Path(sys.argv[2])) else 1)
        except (OSError, ValueError):
            sys.exit(1)
    if '--manifest' in sys.argv[1:]:
        bundle_cli(sys.argv[1:])
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    parser.add_argument('compat', nargs='?')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    if not (directory / args.image).exists():
        parser.error(f'Missing image: {args.image}')
    spec = read_container_inventory(directory, args.image)
    for command in spec.commands:
        path = directory / command
        if path.is_symlink() or (path.exists() and not path.stat().st_mode & 0o111):
            continue
        if path.exists():
            current = path.read_bytes()
            if current == render_wrapper(spec.image_basename, command, legacy=args.compat == "legacy"):
                continue
            from wrapper_legacy import classify_relocated_wrapper, WrapperState
            state, _ = classify_relocated_wrapper(directory, command, current)
            if state not in {WrapperState.LEGACY, WrapperState.FIXED}:
                print(f"[WARN] Preserving customized wrapper: {path}", file=sys.stderr)
                continue
        write_artifact(path, render_wrapper(spec.image_basename, command, legacy=args.compat == "legacy"), True)
    for format, suffix in [('lua', '.lua'), ('tcl', '')]:
        path = directory.parent / 'modules' / spec.module.name / (spec.module.version + suffix)
        content = render_module(spec, format=format)
        if path.is_symlink():
            continue
        if path.is_file():
            content = managed_module_content(path.read_text(), spec, format=format, containers_root=directory.parent)
            if content is None:
                print(f"[WARN] Preserving customized module: {path}", file=sys.stderr)
                continue
        write_artifact(path, content)
    for activate in [True, False]:
        prefix = 'activate' if activate else 'deactivate'
        write_artifact(directory / f'{prefix}_{args.image}.sh', render_activation(args.image, activate), True)
    uninstall = directory / 'ts_uninstall.sh'
    if uninstall.is_file():
        content = re.sub(r'(?ms)^# neurodesk-module-uninstall-begin\n.*?^# neurodesk-module-uninstall-end\n?', '', uninstall.read_text())
        content += '# neurodesk-module-uninstall-begin\n'
        content += '_neurodesk_modules=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/modules\n'
        content += 'rm -f ' + ' '.join('"$_neurodesk_modules/"' + shlex.quote(spec.module.name + '/' + spec.module.version + suffix) for suffix in ['.lua', '']) + '\n'
        content += 'unset _neurodesk_modules\n'
        content += '# neurodesk-module-uninstall-end\n'
        write_artifact(uninstall, content.encode(), True)


if __name__ == '__main__':
    main()
