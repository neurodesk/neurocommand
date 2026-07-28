# Proposal: manifest-backed container distribution

> Status: draft for maintainer review<br>
> Required implementation repositories: `neurodesk/neurocontainers` and
> `neurodesk/neurocommand`<br>
> Conditional implementation repository: `neurodesk/neurodesktop`<br>
> Repository state reviewed: 2026-07-28

## Decision requested

Adopt a versioned release manifest as the contract between Neurocontainers,
OCI registries, CVMFS, Neurocommand, and Neurodesktop, and publish new releases
under:

```text
/containers/<name>/<software-version>_<cvmfs-variant>_<build-date>/
```

For example:

```text
/containers/fsl/6.0.7.16_amd64_20260728/
/containers/fsl/6.0.7.16_arm64_20260728/
/containers/fsl/6.0.7.16_gpu-amd64_20260728/
```

The change has five non-negotiable properties:

1. A release is identified by logical name, software version, build variant,
   and build date. A platform artifact adds an OCI platform such as
   `linux/amd64`.
2. One immutable OCI tag points to an image index, even when the release
   currently has only one platform. Each platform-specific SIF is attached to
   the corresponding child image manifest.
3. The complete existing CVMFS path remains valid for every newly published
   release before generated views expose it. Old clients and stored paths do
   not need to understand manifests.
4. Existing releases are not moved, rewritten, backfilled, or deleted by this
   change.
5. The implementation is delivered as one coordinated merge train with two
   required linked PRs. A Neurodesktop PR is added only if exact-head testing
   proves that source changes are necessary.

This deliberately changes the target distribution model described in
[neurocommand#733](https://github.com/neurodesk/neurocommand/issues/733).
Neurocontainers may still fan out concrete build candidates such as
`fsl_gpu_arm64`, but the published manifest keeps logical name, build variant,
and platform as separate fields. The old concrete name is retained as an
explicit compatibility identity.

## Scope

### Included

- the release identity and schema;
- the new CVMFS release path;
- the complete legacy CVMFS path for each new release;
- multi-platform OCI indexes and platform-specific SIF referrers;
- immutable release history;
- manifest-driven `apps.json`, `cvmfs/log.txt`, modules, launchers, publication,
  and keep-lists;
- publication gating so generated views expose only a complete OCI and CVMFS
  release;
- a shared Neurocommand resolver for new releases;
- exact-head testing across the linked PRs; and
- rollback without deleting immutable content.

### Not included

- converting existing CVMFS releases to the new hierarchy;
- removing flat CVMFS paths, legacy OCI tags, object-store SIFs, or filename
  parsing;
- changing `/cvmfs/neurodesk.ardc.edu.au`, the public module roots, or
  `module load <name>/<version>`;
- redesigning the full release-withdrawal or retention policy;
- deleting any object as part of activation;
- signatures, SBOMs, or attestations beyond leaving room for OCI referrers; or
- unrelated module portability and packaging work.

Keeping these items out is part of the safety case. They can be proposed
separately after the new contract is operating.

## Why this change

### The flat name is an accidental schema

Current code commonly treats a release as:

```text
<name>_<version>_<YYYYMMDD>
```

That string is parsed independently by:

- [`neurodesk/fetch_containers.sh`](neurodesk/fetch_containers.sh);
- [`neurodesk/fetch_and_run.sh`](neurodesk/fetch_and_run.sh);
- [`neurodesk/transparent-singularity/run_transparent_singularity.sh`](neurodesk/transparent-singularity/run_transparent_singularity.sh);
- [`cvmfs/sync_containers_to_cvmfs.sh`](cvmfs/sync_containers_to_cvmfs.sh);
- [`cvmfs/reconcile_module_files.py`](cvmfs/reconcile_module_files.py);
- [`containers.sh`](containers.sh);
- upload, cleanup, and DOI workflows; and
- Neurodesktop's
  [`config/test_neurodesktop.sh`](https://github.com/neurodesk/neurodesktop/blob/main/config/test_neurodesktop.sh).

Draft [neurocommand#767](https://github.com/neurodesk/neurocommand/pull/767)
correctly parses legacy names from the right so concrete names containing
underscores work. That is necessary compatibility hardening, but it should not
become the schema for new releases.

### Two active Neurocontainers changes need one release boundary

Draft [neurocontainers#2913](https://github.com/neurodesk/neurocontainers/pull/2913)
fans one recipe out into architecture and named-variant candidates. Draft
[neurocontainers#2914](https://github.com/neurodesk/neurocontainers/pull/2914)
builds and tests an unprivileged candidate, then promotes the exact tested
Docker archive, SIF, checksums, recipe fingerprint, PR, and commit after merge.

They overlap in build and release workflows. Merging both unchanged would leave
the secure one-PR promotion path AMD64-only while the variant path continues
through the older release flow. This proposal makes their integration, rather
than their independent merge, a prerequisite.

### Presentation metadata is not a safe inventory

`apps.json` is a user-facing view. Releases may be hidden from menus while
their artifacts must still be retained. Neurocommand issue
[#733](https://github.com/neurodesk/neurocommand/issues/733) records ARM SIFs
being removed after cleanup used the UI-derived log as its keep-list.

An immutable release manifest can generate `apps.json` and `cvmfs/log.txt`, but
neither generated view should decide whether an artifact exists or is retained.

## Identity model

The document uses these terms precisely:

| Term | Meaning | Example |
|---|---|---|
| Logical name | Stable recipe/tool identity | `fsl` |
| Software version | Upstream software version | `6.0.7.16` |
| Build variant | Neurodesk build choice, independent of CPU architecture | `default`, `gpu`, `cuda12` |
| Build date | Eight-digit UTC release build identifier | `20260728` |
| Release | One logical build across its supported platforms | `fsl/6.0.7.16/default/20260728` |
| Platform artifact | One release plus an OCI platform | release + `linux/arm64` |
| CVMFS variant | Filesystem projection of build variant and platform | `arm64`, `gpu-amd64` |
| Legacy identity | Complete concrete name used by existing clients | `fsl_arm64_6.0.7.16_20260728` |

The release identity is:

```text
(name, software_version, build_variant, build_date)
```

The platform artifact identity is:

```text
(name, software_version, build_variant, build_date, platform)
```

This distinction matters. One release manifest and OCI image index can describe
multiple platform artifacts.

### Normalization

- Platforms use OCI values such as `linux/amd64` and `linux/arm64`.
- Recipe inputs such as `x86_64` and `aarch64` are normalized during the build.
- Build variants use stable lower-case identifiers such as `default`, `gpu`,
  and `cuda12`.
- The schema validates every value before using it in a path or OCI tag.
- `/` and path traversal are forbidden in path components.
- Clients never recover fields by splitting a path, display name, or OCI tag.

The 230 current recipe versions reviewed for this proposal fit OCI tag
components. The implementation PR must repeat that audit in CI and use one
checked-in validation rule rather than introducing different shell and Python
rules.

### CVMFS variant projection

The manifest stores the projection explicitly:

| Build variant | Platform | CVMFS variant |
|---|---|---|
| `default` | `linux/amd64` | `amd64` |
| `default` | `linux/arm64` | `arm64` |
| `gpu` | `linux/amd64` | `gpu-amd64` |
| `gpu` | `linux/arm64` | `gpu-arm64` |
| `cuda12` | `linux/amd64` | `cuda12-amd64` |

The order is always `<build-variant>-<architecture>`, with `default` omitted.
Consumers read `cvmfs_variant` from the manifest; they do not reproduce this
rule.

### Immutability

An immutable tag, manifest path, or CVMFS release directory must not be
overwritten with different content. Promotion fails when the same
`(name, software_version, build_variant, build_date)` already exists with
another digest.

This proposal retains the requested `YYYYMMDD` build date. A second, different
same-day build is rejected. Extending the build identifier is a future schema
decision, not an implicit suffix added by one workflow.

## CVMFS layout and backwards compatibility

### Canonical release

A newly published AMD64 release looks like:

```text
/cvmfs/neurodesk.ardc.edu.au/
└── containers/
    └── fsl/
        └── 6.0.7.16_amd64_20260728/
            ├── .cvmfscatalog
            ├── release.json
            ├── commands.txt
            ├── fsl_6.0.7.16_20260728.simg/
            └── <existing generated wrappers and metadata>
```

The inner layout intentionally remains compatible with today's publisher. In
particular, this proposal does **not** rename the unpacked `.simg` directory to
`rootfs` or move wrappers into a new `bin` directory. Those changes are not
needed to introduce the hierarchy and would increase the activation surface.

The release directory is immutable after publication and is a nested catalog
root. The current nested catalogs inside the unpacked image are preserved until
separate measurements justify changing them.

### Required legacy path

The same CVMFS transaction creates the canonical directory and the old outer
name:

```text
/containers/fsl_6.0.7.16_20260728
  -> fsl/6.0.7.16_amd64_20260728
```

The complete old path therefore still works:

```text
/containers/fsl_6.0.7.16_20260728/fsl_6.0.7.16_20260728.simg
```

ARM and named variants use manifest-declared legacy identities:

```text
/containers/fsl_arm64_6.0.7.16_20260728
  -> fsl/6.0.7.16_arm64_20260728

/containers/fsl_gpu_6.0.7.16_20260728
  -> fsl/6.0.7.16_gpu-amd64_20260728
```

This meets the requested hard-link intent—one payload with two stable
namespaces—but the outer directory must be a symbolic link, not a literal
hard link. POSIX does not support directory hard links, and CVMFS emulates file
hard-link groups only within one directory. A directory symlink also avoids
duplicating catalog entries for the unpacked image.

If the exact-head integration test finds an old client that cannot use the
directory symlink, the safe fallback is a generated compatibility directory
whose payload entries have the same CVMFS content hashes. CVMFS
content-addressed storage avoids duplicate payload blobs, although the
compatibility directory would add catalog entries. The linked PR must record
which representation passed testing; it must not silently omit the old path.

### Existing releases

Existing flat releases remain exactly where they are. Neurocommand continues
to resolve them through the legacy path because they have no schema-v1 release
manifest. This change does not create canonical aliases for the existing
catalogue and does not rewrite existing nested catalogs.

That is a permanent supported condition, not an intermediate deployment state:

- old release without schema v1 → legacy resolver;
- new release with valid schema v1 → manifest resolver;
- old client accessing a new release → manifest-declared legacy CVMFS path.

### Publication transaction

For one release, Neurocommand opens one CVMFS transaction and:

1. materializes the canonical release directory;
2. writes the exact promoted `release.json`;
3. preserves the current unpacked `.simg`, wrapper, command, and nested-catalog
   shape;
4. creates the complete legacy directory alias;
5. generates or updates modules and compatibility metadata;
6. verifies both full paths, a non-empty `commands.txt`, module resolution, and
   an Apptainer/Singularity execution through each path; and
7. publishes only if every check passes.

Failure aborts the transaction, leaving the previous CVMFS revision visible.
No cleanup runs inside this transaction.

## OCI layout

### Repository and tags

Use one repository per logical tool:

```text
quay.io/neurodesk/<name>
ghcr.io/neurodesk/<name>
```

Immutable default-variant tag:

```text
<software-version>_<build-date>
```

Immutable named-variant tag:

```text
<software-version>_<build-variant>_<build-date>
```

For example:

```text
quay.io/neurodesk/fsl:6.0.7.16_20260728
quay.io/neurodesk/fsl:6.0.7.16_gpu_20260728
```

Architecture is not encoded in the repository or tag. Floating tags such as
`<software-version>` or `latest` remain convenience pointers and are advanced
last. They are never stored as reproducibility references.

### Subject graph

Every immutable release tag resolves to an OCI image index:

```text
fsl:6.0.7.16_20260728
├── linux/amd64 image manifest
│   └── SIF artifact manifest -> tested AMD64 SIF blob
├── linux/arm64 image manifest
│   └── SIF artifact manifest -> tested ARM64 SIF blob
└── Neurodesk release-manifest artifact
```

An index is used even for a release with one platform so clients and promotion
code have one stable shape.

The SIF artifact manifest has:

```text
artifactType: application/vnd.sylabs.sif.layer.v1.sif
subject:       <exact platform child image-manifest digest>
```

The release-manifest artifact has the top-level image index as its subject. The
release manifest records the selected child and SIF artifact digests, so a
runtime does not choose `.manifests[0]` from an untrusted referrers response.

The publisher supports the OCI 1.1 referrers API and the standard fallback tag
schema. Each registry listed in a release manifest must contain a complete,
verified subject graph. A release requires at least one complete registry; a
best-effort mirror is listed only after its copy is verified.

### Promotion order

Immutable candidate objects are not active merely because they exist in a
registry. Trusted promotion:

1. verifies every required build-variant/platform candidate against its PR head
   SHA and recipe fingerprint;
2. pushes and verifies the child image manifests and tested SIF artifacts, and
   verifies the manifest-declared legacy object-store keys;
3. creates and verifies the complete image index;
4. resolves post-push digests for every recorded registry;
5. generates and validates the immutable release manifest;
6. attaches the release manifest to the index and commits the same JSON to
   trusted release history;
7. asks Neurocommand to publish the canonical and legacy CVMFS paths; and
8. after Neurocommand acknowledges that CVMFS revision, updates generated
   presentation views and floating tags.

A failure before step 6 leaves only immutable registry objects. A failure in
steps 6 or 7 leaves an immutable manifest in release history but no generated
view selects it. A partial platform set or missing legacy CVMFS path is never
represented as an available release.

Legacy object-store SIFs remain published and retained by this change. They are
compatibility inputs and rollback assets, not the source of truth for new
releases.

## Release manifest

### Ownership and storage

Neurocontainers owns the schema and immutable release history:

```text
schemas/container-release-v1.schema.json
releases/<name>/<software-version>/<build-variant>/<build-date>.json
```

The candidate manifest introduced by `neurodesk/neurocontainers#2914` and the
release manifest in this proposal are different:

- the candidate manifest binds untrusted PR outputs to a tested commit before
  promotion;
- the release manifest records the verified, post-push distribution graph.

The release manifest is immutable and contains no mutable lifecycle `status`.
Committing it to trusted release history records a published distribution
graph, but does not by itself make the release selectable. Selection occurs
only after Neurocommand acknowledges the CVMFS revision containing both paths
and promotion regenerates the presentation views. A future withdrawal can
change those views or add a separate tombstone while retaining the historical
manifest and digests; that mechanism is outside this proposal.

The identical release JSON is:

- committed by the trusted Neurocontainers promotion workflow;
- attached to the OCI image index;
- copied into the canonical CVMFS release directory; and
- consumed by Neurocommand to generate compatibility and presentation views.

Neurocommand vendors or fetches the schema with a recorded SHA-256. CI in both
repositories must fail if their schema bytes or fixtures differ.

### Illustrative schema

This example establishes field relationships; the implementation PR supplies
the final JSON Schema:

```json
{
  "schema_version": 1,
  "release_id": "fsl/6.0.7.16/default/20260728",
  "name": "fsl",
  "software_version": "6.0.7.16",
  "build_variant": "default",
  "build_date": "20260728",
  "source": {
    "repository": "neurodesk/neurocontainers",
    "recipe": "recipes/fsl",
    "git_commit": "0123456789abcdef",
    "recipe_fingerprint": "sha256:..."
  },
  "oci": {
    "immutable_tag": "6.0.7.16_20260728",
    "registries": [
      {
        "repository": "quay.io/neurodesk/fsl",
        "index_digest": "sha256:...",
        "platforms": {
          "linux/amd64": {
            "image_manifest_digest": "sha256:...",
            "sif_manifest_digest": "sha256:...",
            "sif_blob_digest": "sha256:...",
            "sif_sha256": "..."
          },
          "linux/arm64": {
            "image_manifest_digest": "sha256:...",
            "sif_manifest_digest": "sha256:...",
            "sif_blob_digest": "sha256:...",
            "sif_sha256": "..."
          }
        }
      }
    ]
  },
  "platforms": {
    "linux/amd64": {
      "cvmfs_variant": "amd64",
      "cvmfs_release_path": "containers/fsl/6.0.7.16_amd64_20260728",
      "cvmfs_payload_path": "containers/fsl/6.0.7.16_amd64_20260728/fsl_6.0.7.16_20260728.simg",
      "legacy_release_path": "containers/fsl_6.0.7.16_20260728",
      "legacy_payload_path": "containers/fsl_6.0.7.16_20260728/fsl_6.0.7.16_20260728.simg",
      "legacy_object_keys": ["fsl_6.0.7.16_20260728.simg"]
    },
    "linux/arm64": {
      "cvmfs_variant": "arm64",
      "cvmfs_release_path": "containers/fsl/6.0.7.16_arm64_20260728",
      "cvmfs_payload_path": "containers/fsl/6.0.7.16_arm64_20260728/fsl_arm64_6.0.7.16_20260728.simg",
      "legacy_release_path": "containers/fsl_arm64_6.0.7.16_20260728",
      "legacy_payload_path": "containers/fsl_arm64_6.0.7.16_20260728/fsl_arm64_6.0.7.16_20260728.simg",
      "legacy_object_keys": ["fsl_arm64_6.0.7.16_20260728.simg"]
    }
  },
  "deploy": {
    "bins": ["fslmaths"],
    "paths": ["/opt/fsl/bin"],
    "apptainer_args": []
  },
  "apps": [
    {
      "id": "fsl",
      "label": "FSL 6.0.7.16",
      "exec": "",
      "terminal": true
    },
    {
      "id": "fsleyes",
      "label": "FSLeyes 6.0.7.16",
      "exec": "fsleyes",
      "terminal": false
    }
  ],
  "categories": [
    "functional imaging",
    "structural imaging"
  ]
}
```

Required identity, source, platform, digest, path, and deploy fields fail
validation when absent. Unknown additive fields may be accepted within schema
version 1, but they cannot change the meaning of an existing field.

## Resolution and generated views

For a schema-v1 release, the shared Neurocommand resolver:

1. loads the exact `release_id` named by a generated launcher or module entry
   from trusted release history;
2. validates the schema and release identity;
3. normalizes the host platform and selects its exact platform entry;
4. uses `cvmfs_payload_path` when it exists;
5. otherwise pulls the recorded SIF artifact manifest digest from the first
   complete reachable registry and verifies its subject and blob checksum; and
6. reports the release ID, platform, path, and digests on failure.

For a release without a valid schema-v1 manifest, Neurocommand uses the existing
legacy resolver. This capability detection is automatic; there is no manually
coordinated feature flag.

The same resolver library or CLI is used by launchers, local download,
CVMFS preflight, full tests, and diagnostics. Shell entrypoints consume
structured output rather than implementing separate registry and naming logic.

### Compatibility outputs

For new releases, Neurocommand generates from the manifest:

- `apps.json`;
- `cvmfs/log.txt`;
- the CVMFS publication inventory;
- modulefiles and launchers; and
- cleanup keep-list entries for both canonical and legacy identities.

The formats consumed by old clients remain valid. `apps.json` and
`cvmfs/log.txt` are generated views, not release authority, and are updated
only after the CVMFS publication acknowledgement. In particular,
`cvmfs/log.txt` continues to list the manifest's legacy concrete identity so
existing scripts and cleanup jobs see the same name they understand.

The public module roots and default user interface remain:

```text
/cvmfs/neurodesk.ardc.edu.au/containers/modules
/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules
module load fsl/6.0.7.16
```

The default module selects the host platform. A named build variant is exposed
without changing the logical tool name:

```text
module load fsl/6.0.7.16-gpu
```

If a concrete module name such as `fsl_gpu/6.0.7.16` was previously published,
it remains as a compatibility alias. Module generation may call a
CVMFS-hosted dispatcher to choose the manifest's platform path, but existing
module names and roots do not disappear in this change.

### Cleanup boundary

Before producer activation, the Neurocommand PR adds schema-v1 releases and
their compatibility identities to the keep set. Existing cleanup may continue
under its current policy, but the linked change must not make either namespace
eligible for deletion and must not delete legacy OCI, object-store, or CVMFS
content.

The exact-head cleanup dry run must prove that:

- every release manifest is reachable independently of menu visibility;
- canonical and legacy CVMFS paths are treated as one retained payload;
- every recorded child manifest and referrer is retained; and
- existing releases without schema v1 remain in the legacy keep set.

Changing the broader retention or withdrawal policy is outside this proposal.

## Repository ownership

### Neurocontainers

Neurocontainers owns:

- recipe build-variant and platform declarations;
- candidate fan-out and testing;
- candidate-to-promotion verification;
- the release JSON Schema and immutable history;
- OCI image indexes, child manifests, and SIF referrers;
- post-push digest verification; and
- generation of trusted release inputs for Neurocommand.

The producer PR must integrate the fan-out in
`neurodesk/neurocontainers#2913` with the promotion boundary in
`neurodesk/neurocontainers#2914`. The two drafts should not merge independently
in their current form.

### Neurocommand

Neurocommand owns:

- schema validation and platform selection;
- manifest-driven generated views;
- canonical and legacy CVMFS publication in one transaction;
- modules, launchers, and local download resolution;
- legacy parsing and fallback;
- compatibility keep-list generation; and
- failure diagnostics.

The legacy right-hand parser and deployment safety checks in
`neurodesk/neurocommand#767` remain useful. New schema-v1 paths are read from
the manifest, not parsed.

### Neurodesktop

Neurodesktop continues to consume:

- the same CVMFS mount;
- the same public module roots;
- the same module names;
- the same local container root; and
- valid legacy CVMFS paths.

Its current hard-coded container test should pass unchanged through the legacy
alias. That is valuable compatibility evidence, not automatically a reason to
change Neurodesktop. A source PR is opened only if the exact-head run exposes a
real incompatibility.

## One coordinated merge train

### Linked PR set

| Order | Repository | Required contents | Activation behaviour |
|---|---|---|---|
| 1 | `neurodesk/neurocommand` | Schema consumer, resolver, manifest-generated compatibility outputs, canonical plus legacy CVMFS publication, module dispatch, keep-list hardening, and tests. | Dormant for releases without schema v1; legacy behaviour remains. |
| 2 | `neurodesk/neurodesktop` | Only the smallest source change demonstrated necessary by exact-head testing. | Conditional; no PR is preferred when current source passes. |
| 3 | `neurodesk/neurocontainers` | One integrated or superseding implementation of `neurodesk/neurocontainers#2913` and `neurodesk/neurocontainers#2914`: matrix candidates, complete promotion, schema, OCI graph, and immutable release history. | Merged last; the first trusted schema-v1 release activates the new path for that release. |

There are two required implementation PRs, not three. If Neurodesktop needs no
source change, its tested commit or image digest is recorded on the two required
PRs and this tracker.

Every implementation PR body contains:

```text
Part of neurodesk/neurocommand#777
Depends on <exact linked PR URLs>
Tested with:
- neurocommand: <commit SHA>
- neurocontainers: <commit SHA>
- neurodesktop: <commit SHA or image digest>
Merge order: neurocommand -> neurodesktop (only if required) -> neurocontainers
```

The Neurocontainers PR also states explicitly whether
`neurodesk/neurocontainers#2913` and `neurodesk/neurocontainers#2914` are
integrated, superseded, or closed in favour of it. The Neurocommand PR does the
same for
`neurodesk/neurocommand#767`: it either uses that PR as its base, incorporates
its legacy fixes, or supersedes it with equivalent tests.

### Exact-head proof before merge

All linked PRs remain draft until one reproducible run checks out their exact
head commits. That run builds a release fixture containing:

- `default` on AMD64 and ARM64 under one OCI index;
- one named build variant such as `gpu`;
- an immutable release manifest with exact registry and CVMFS paths; and
- generated old and new Neurocommand views.

It then proves:

- schema equality and positive/negative validation in both repositories;
- candidate tamper detection for source, platform, and every recorded digest;
- refusal to promote a missing or failed platform child;
- native-referrers and fallback-tag publication;
- exact SIF selection by recorded digest;
- canonical and full legacy CVMFS paths execute the same content;
- the unchanged Neurodesktop container test can use the legacy path;
- modules work on AMD64 and ARM64 without changing their public name or root;
- CVMFS-disabled and local/offline paths work;
- a missing, incomplete, or unsupported manifest returns to legacy resolution;
- registry failure uses only another complete, verified registry entry;
- CVMFS failure aborts publication and leaves the previous revision visible;
  and
- cleanup dry-run retains existing, hidden, canonical, and legacy releases.

The manifest, resolved digests, generated-file diff, CVMFS path listing, test
results, and tested SHAs are linked from each required PR. Testing branch names
or moving default branches is not sufficient evidence.

### Merge and activation window

The linked PRs merge in the table order during one scheduled window. This is an
ordered merge, not a multi-stage deployment:

1. merge the backwards-compatible Neurocommand consumer;
2. merge the Neurodesktop compatibility fix only if the proof required one;
3. merge the integrated Neurocontainers producer; and
4. promote one already-tested schema-v1 release and observe its OCI, CVMFS, old
   path, new path, module, and Neurodesktop checks before closing the window.

GitHub, OCI registries, and CVMFS cannot share one distributed transaction.
Safety comes from two per-release commit points:

- trusted release history records only a complete OCI graph; and
- one CVMFS transaction publishes the canonical and legacy paths together.

Generated views select the release only after both commit points succeed. A new
client can still use the recorded OCI SIF when CVMFS is temporarily unavailable
after activation. An old client first sees the release only after its legacy
CVMFS path is published.

### Go/no-go checklist

The merge window proceeds only when:

- [ ] maintainers approve the identity and legacy-path decisions in this
  document;
- [ ] the two required PRs link each other and pin the same schema and fixtures;
- [ ] `neurodesk/neurocontainers#2913` and `neurodesk/neurocontainers#2914`
  have one agreed integration outcome;
- [ ] `neurodesk/neurocommand#767` has one agreed prerequisite, integration, or
  supersession outcome;
- [ ] all repository-local and exact-head tests pass;
- [ ] the unchanged Neurodesktop test passes, or its conditional PR is ready;
- [ ] OCI promotion and CVMFS publication failure injection passes;
- [ ] generated views remain unchanged when CVMFS publication or its
  acknowledgement fails;
- [ ] previous generated metadata, OCI pointer digests, and CVMFS revision are
  recorded;
- [ ] destructive cleanup and legacy removal are absent from the diff; and
- [ ] maintainers for the affected repositories confirm the window.

Failure of any item stops the merge train. It does not create another supported
mode or justify bypassing compatibility.

## Rollback

Rollback changes selection and generated views; it does not rebuild or delete
immutable content.

- Before a release manifest enters trusted release history, the pushed
  candidate graph is inert.
- If promotion fails, do not commit the release manifest or advance floating
  tags.
- If CVMFS publication fails after the history commit, leave generated views
  and floating tags unchanged; the unselected immutable manifest may remain.
- If activation fails after generated views change, restore the previous
  `apps.json`, `cvmfs/log.txt`, module views, and floating-tag digests.
- Abort an unpublished CVMFS transaction. If publication completed, restore the
  recorded previous CVMFS revision.
- A new client automatically returns to legacy resolution when the schema-v1
  release is no longer selected.
- Existing clients continue using unchanged legacy releases and the retained
  legacy paths of new releases.

The immutable release manifest and candidate objects may remain for diagnosis;
they are not selected and are not deleted during rollback.

## Expected code impact

This is a review map, not permission to expand the change beyond the stated
scope.

| Repository | Area | Required change |
|---|---|---|
| Neurocontainers | `builder/variants.py` and callers in `neurodesk/neurocontainers#2913` | Preserve logical name, build variant, platform, and legacy concrete identity as separate fields. |
| Neurocontainers | candidate/promotion code in `neurodesk/neurocontainers#2914` | Matrix over all required candidates and bind every artifact to the tested PR head. |
| Neurocontainers | release generation | Validate schema, retain build history, record post-push digests, and generate the OCI graph. |
| Neurocommand | `fetch_and_run.sh`, `fetch_containers.sh`, transparent-singularity | Call one manifest resolver for schema-v1 releases and retain legacy fallback. |
| Neurocommand | `sync_containers_to_cvmfs.sh` | Consume release manifests and publish canonical plus legacy paths atomically. |
| Neurocommand | `reconcile_module_files.py` | Use explicit manifest identity for new releases and retain the legacy parser. |
| Neurocommand | `write_log.py`, `build_menu.py`, `containers.sh` | Generate compatibility views without parsing presentation labels. |
| Neurocommand | upload/cleanup and DOI workflows | Use manifest inventory and keep both identities; perform no activation-time deletion. |
| Neurodesktop | `environment_variables.sh`, `test_neurodesktop.sh` | Expected to remain unchanged; use them as compatibility tests. |

## Alternatives considered

### Keep the flat layout and parse from the right

This is required for old releases and is correctly improved by
`neurodesk/neurocommand#767`. It does not provide a durable release schema,
multi-platform OCI selection, or a safe inventory, so it remains a
compatibility adapter.

### Publish every concrete variant as a separate logical name

This is the current direction in `neurodesk/neurocommand#733` and the first
implementation in `neurodesk/neurocontainers#2913`. It is simple for legacy
consumers, but makes ARM a different tool, creates separate OCI repositories,
and conflicts with the same-tag multi-architecture direction accepted in
[neurocontainers#2593](https://github.com/neurodesk/neurocontainers/issues/2593).
This proposal retains those concrete names only as explicit compatibility
identities.

### Put architecture in separate OCI tags

That moves platform selection into custom tag naming and duplicates logic that
OCI image indexes already standardize. Architecture-specific candidate tags may
exist during a build, but they are not public release identities.

### Attach every SIF to the top-level index

A SIF is platform-specific. Attaching it to the exact child image manifest
gives it an unambiguous OCI platform subject and lets the release manifest
record one exact SIF per platform.

### Rename the unpacked payload to `rootfs`

That name is clearer than a directory ending in `.simg`, but it would require
another compatibility alias inside every release and changes more publisher,
module, maintenance, and test code. Keeping the existing inner name is safer
for this change.

### Change Neurodesktop at the same time

Changing code merely because it constructs an old path would weaken the proof
that old paths remain compatible. The current Neurodesktop test should pass
unchanged. A third PR is justified only by a demonstrated failure.

## Consequences

The proposal intentionally accepts:

- two CVMFS namespace entries for every new payload;
- a permanent legacy resolver for historical releases;
- a schema and generated compatibility views that must be versioned and tested
  across repositories;
- an OCI index even for single-platform releases;
- rejection rather than overwrite for same-day conflicting builds; and
- a larger coordinated implementation review in exchange for avoiding
  long-lived mixed contracts.

It avoids:

- moving existing containers;
- duplicating unpacked payload data in the normal compatibility representation;
- encoding platform into a logical tool name;
- choosing artifacts by list order or floating tags;
- using menu visibility as retention truth; and
- requiring a Neurodesktop source change without evidence.

## Maintainer ratification

Approval of this document means agreement that:

- [ ] release identity excludes platform, while platform artifact identity adds
  it;
- [ ] build variant and platform remain separate manifest fields;
- [ ] the CVMFS variant is their explicit stored projection;
- [ ] logical names remain stable in the target OCI/CVMFS model, superseding
  concrete platform names as the target from
  `neurodesk/neurocommand#733`;
- [ ] SIFs attach to platform child manifests;
- [ ] every new canonical CVMFS release includes its full manifest-declared
  legacy path in the same transaction;
- [ ] the existing inner `.simg` layout, legacy module aliases, and public
  module roots remain;
- [ ] existing releases are not backfilled or removed;
- [ ] the immutable release manifest contains no mutable lifecycle status, and
  generated views activate it only after CVMFS acknowledges both paths; and
- [ ] implementation uses two required linked PRs, with Neurodesktop conditional
  on a failing compatibility test.

Once these boxes are agreed, the linked implementation PRs can be opened and
reviewed as one contract without further design stages.

## Relevant discussions and specifications

- [neurocommand#733: ARM and variant publication](https://github.com/neurodesk/neurocommand/issues/733)
- [neurocommand#767: legacy named-variant compatibility](https://github.com/neurodesk/neurocommand/pull/767)
- [neurocontainers#1092: general container variations](https://github.com/neurodesk/neurocontainers/issues/1092)
- [neurocontainers#2408: v2 OCI repository and tag layout](https://github.com/neurodesk/neurocontainers/issues/2408)
- [neurocontainers#2593: same-tag multi-architecture direction](https://github.com/neurodesk/neurocontainers/issues/2593)
- [neurocontainers#2796: fulltest selection from release metadata](https://github.com/neurodesk/neurocontainers/issues/2796)
- [neurocontainers#2913: first-class variant fan-out](https://github.com/neurodesk/neurocontainers/pull/2913)
- [neurocontainers#2914: tested candidate promotion](https://github.com/neurodesk/neurocontainers/pull/2914)
- [OCI image index specification](https://github.com/opencontainers/image-spec/blob/main/image-index.md)
- [OCI image manifest specification](https://github.com/opencontainers/image-spec/blob/main/manifest.md)
- [OCI Distribution 1.1 referrers and fallback tag schema](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)
- [Apptainer OCI registry documentation](https://apptainer.org/docs/user/latest/registry.html)
- [CVMFS nested catalogs](https://cvmfs.readthedocs.io/en/2.14/cpt-repo/#managing-nested-catalogs)
- [CVMFS content hashes and hard-link constraints](https://cvmfs.readthedocs.io/en/2.14/cpt-details/#file-catalog)
