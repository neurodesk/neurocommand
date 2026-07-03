#!/usr/bin/env python3
"""Consolidate pending neurodesk/apps.json updates into a single queue PR.

Rules implemented:
- Process queue sources in created_at order. A source is either an open PR
  that touches neurodesk/apps.json or the PR-less bot branch that the
  neurocontainers release automation force-pushes (``--source-branch``).
- Compute each source's changed tools using merge-base(main, source_head).
- Apply those tool changes to a consolidated apps.json snapshot.
- Later sources overwrite earlier sources for the same tool.
- With ``--merge-consolidated`` (scheduled/manual runs), squash-merge the
  consolidated PR directly via the REST API. Auto-merge cannot be used here:
  the repository disallows it, and with no required status checks GitHub
  rejects enabling auto-merge on an already-mergeable PR anyway.
- Close source PRs (apps.json plus synced icons only) once their changes are
  in main — either merged in this run or already contained in the base.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Reuse the recipe icon sync so apps.json updates carry their icons inline.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_neurocontainer_icons import sync_icons  # noqa: E402

DIFF_COMMENT_MARKER = "<!-- appsjson-consolidation-diff -->"
MAX_PR_BODY_DIFF_CHARS = 15_000
MAX_PR_COMMENT_DIFF_CHARS = 55_000


@dataclass
class PullRequest:
    number: int
    created_at: str
    title: str
    html_url: str
    files: List[str]

    def consolidatable(self, target_file: str, icons_prefix: str) -> bool:
        """True when the PR only touches apps.json and its synced icons.

        Generator PRs ship icon PNGs alongside apps.json, and the consolidated
        branch re-syncs those icons itself, so such PRs are fully represented
        by the consolidation and safe to close.
        """
        return all(
            path == target_file or path.startswith(icons_prefix)
            for path in self.files
        )


@dataclass
class QueueSource:
    """One ordered input to the queue: an open PR or the bot source branch."""

    created_at: str
    ref: str
    label: str
    title: str
    pr: Optional[PullRequest] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--target-file", default="neurodesk/apps.json")
    parser.add_argument("--consolidated-branch", default="bot/appsjson-consolidated")
    parser.add_argument(
        "--source-branch",
        default="update-apps-json",
        help=(
            "PR-less branch that the neurocontainers release automation "
            "force-pushes; consumed as a queue source when it exists."
        ),
    )
    parser.add_argument(
        "--merge-consolidated",
        action="store_true",
        help="Squash-merge the consolidated PR via the REST API at the end of the run.",
    )
    parser.add_argument(
        "--merge-method",
        choices=["merge", "squash", "rebase"],
        default="squash",
    )
    parser.add_argument(
        "--neurocontainers-path",
        type=Path,
        default=Path("neurocontainers"),
        help="Path to a checkout of NeuroDesk/neurocontainers used for icon sync.",
    )
    parser.add_argument(
        "--icons-dir",
        type=Path,
        default=Path("neurodesk/icons"),
        help="Directory where decoded PNG icons are stored.",
    )
    parser.add_argument(
        "--skip-icon-sync",
        action="store_true",
        help="Do not sync recipe icons into the consolidated branch.",
    )
    return parser.parse_args()


def run_git(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed with code {result.returncode}:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def require_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    return token


def github_request(
    method: str,
    api_url: str,
    path: str,
    token: str,
    query: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Any:
    url = f"{api_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query, doseq=True)}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = Request(
        url,
        method=method,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    with urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        if not body:
            return None
        return json.loads(body)


def github_paginated_get(
    api_url: str,
    path: str,
    token: str,
    query: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    items: List[Any] = []
    page = 1
    while True:
        page_query = dict(query or {})
        page_query.update({"per_page": 100, "page": page})
        chunk = github_request("GET", api_url, path, token, query=page_query)
        if not chunk:
            break
        items.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return items


def list_open_pull_requests(api_url: str, repo: str, token: str) -> List[Dict[str, Any]]:
    prs = github_paginated_get(
        api_url,
        f"/repos/{repo}/pulls",
        token,
        query={"state": "open", "sort": "created", "direction": "asc"},
    )
    prs.sort(key=lambda pr: (pr["created_at"], pr["number"]))
    return prs


def list_pull_request_files(api_url: str, repo: str, pr_number: int, token: str) -> List[str]:
    files = github_paginated_get(
        api_url,
        f"/repos/{repo}/pulls/{pr_number}/files",
        token,
    )
    return [item["filename"] for item in files]


def find_open_head_pr(
    api_url: str,
    repo: str,
    owner: str,
    head_branch: str,
    base_ref: str,
    token: str,
) -> Optional[Dict[str, Any]]:
    prs = github_request(
        "GET",
        api_url,
        f"/repos/{repo}/pulls",
        token,
        query={"state": "open", "head": f"{owner}:{head_branch}", "base": base_ref, "per_page": 1},
    )
    if prs:
        return prs[0]
    return None


def read_json_from_git(ref: str, path: str) -> Dict[str, Any]:
    result = run_git(["show", f"{ref}:{path}"], check=False)
    if result.returncode != 0:
        return {}
    payload = result.stdout.strip()
    if not payload:
        return {}
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Expected top-level JSON object in {path} at {ref}")
    return parsed


def parse_timestamp(value: str) -> datetime:
    """Normalise GitHub API and ``git log %cI`` timestamps for ordering."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def changed_tools(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    keys = set(before.keys()) | set(after.keys())
    changed = [tool for tool in keys if before.get(tool) != after.get(tool)]
    changed.sort()
    return changed


def sort_top_level_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``payload`` with only its top-level (tool) keys sorted.

    The neurocontainers release generator emits tools in alphabetical order but
    preserves each tool's field order (e.g. ``version``/``exec``/``apptainer_args``
    and ``apps``/``categories``). Sorting recursively (``sort_keys=True``) would
    reorder those nested fields and reintroduce churn against the generator's
    output, so we canonicalise only the top level. New tools added during
    consolidation land as appended keys; sorting here puts them in their
    alphabetical place so this repo's apps.json stays byte-compatible with the
    generator and generator PRs show real changes only.
    """
    return {key: payload[key] for key in sorted(payload)}


def write_json(path: str, payload: Dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # No trailing newline: the neurocontainers generator emits the file with a
    # bare ``json.dump(..., indent=4)`` and gates its PRs on ``git diff --quiet``,
    # so matching it byte-for-byte (top-level sorted, no final newline) is what
    # keeps generator PRs free of reorder/newline-only churn.
    out.write_text(json.dumps(sort_top_level_keys(payload), indent=4), encoding="utf-8")


def render_appsjson_diff(
    target_file: str,
    base_payload: Dict[str, Any],
    consolidated_payload: Dict[str, Any],
) -> str:
    # Match write_json's canonical form (top-level keys sorted, no trailing
    # newline) so the diff shown in the PR body reflects what is actually
    # committed to the consolidated branch.
    base_text = json.dumps(sort_top_level_keys(base_payload), indent=4)
    consolidated_text = json.dumps(sort_top_level_keys(consolidated_payload), indent=4)
    diff_lines = difflib.unified_diff(
        base_text.splitlines(),
        consolidated_text.splitlines(),
        fromfile=f"a/{target_file}",
        tofile=f"b/{target_file}",
        lineterm="",
    )
    return "\n".join(diff_lines)


def truncate_diff(diff_text: str, max_chars: int) -> tuple[str, bool]:
    if len(diff_text) <= max_chars:
        return diff_text, False

    if max_chars < 100:
        return diff_text[:max_chars], True

    clipped = diff_text[: max_chars - 40]
    if "\n" in clipped:
        clipped = clipped.rsplit("\n", 1)[0]
    return f"{clipped}\n... (diff truncated)", True


def build_consolidation_pr_body(
    base_ref: str,
    target_file: str,
    sources: List[QueueSource],
    applied_tools: Dict[str, str],
    closed_prs: List[PullRequest],
    should_have_consolidated_pr: bool,
    appsjson_diff: str,
) -> str:
    lines: List[str] = []
    lines.append("# apps.json Queue Consolidation")
    lines.append("")
    lines.append(f"Base branch: `{base_ref}`")
    lines.append(f"Target file: `{target_file}`")
    lines.append("")

    lines.append("## Source Processing Order")
    if sources:
        for source in sources:
            lines.append(f"- {source.label} ({source.created_at}): {source.title}")
    else:
        lines.append("- No pending sources currently modify `neurodesk/apps.json`.")

    lines.append("")
    lines.append("## Final Tool Winners")
    if applied_tools:
        for tool in sorted(applied_tools.keys()):
            lines.append(f"- `{tool}` -> {applied_tools[tool]}")
    else:
        lines.append("- No tool-level changes were applied.")

    lines.append("")
    lines.append("## Source PRs Closed By Consolidation")
    if closed_prs:
        for pr in closed_prs:
            lines.append(f"- #{pr.number}: {pr.title}")
    else:
        lines.append("- None")

    lines.append("")
    lines.append("## Consolidated PR State")
    if should_have_consolidated_pr:
        lines.append("- A consolidated PR is required because queue output differs from `main`.")
    else:
        lines.append("- No consolidated PR is required (queue output matches `main`).")

    lines.append("")
    lines.append("## Proposed `apps.json` Changes")
    if appsjson_diff:
        diff_for_body, is_truncated = truncate_diff(appsjson_diff, MAX_PR_BODY_DIFF_CHARS)
        lines.append("```diff")
        lines.append(diff_for_body)
        lines.append("```")
        if is_truncated:
            lines.append("")
            lines.append(
                "_Diff truncated in PR body due to size. Full latest diff is posted in a PR comment._"
            )
    else:
        lines.append("- No net changes.")

    return "\n".join(lines) + "\n"


def stage_and_push_branch(
    base_ref: str,
    head_branch: str,
    files: Iterable[str],
    commit_message: str,
) -> bool:
    run_git(["config", "user.name", "github-actions[bot]"])
    run_git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])

    run_git(["checkout", "-B", head_branch, f"refs/remotes/origin/{base_ref}"])
    run_git(["add", *files])

    diff_check = run_git(["diff", "--cached", "--quiet"], check=False)
    if diff_check.returncode == 0:
        return False

    run_git(["commit", "-m", commit_message])
    run_git(["push", "--force", "origin", f"HEAD:{head_branch}"])
    return True


def sync_consolidated_icons(
    neurocontainers_path: Path,
    icons_dir: Path,
    apps_json_path: str,
) -> List[str]:
    """Decode any missing recipe icons for the consolidated apps.json.

    Returns the repository-relative paths of newly written icon files so they
    can be staged alongside apps.json on the consolidated branch. A failure
    here (e.g. a malformed upstream icon) is logged but never blocks apps.json
    consolidation; the icon-coverage test will surface a still-missing icon on
    the consolidated PR.
    """
    recipes_path = neurocontainers_path / "recipes"
    if not recipes_path.is_dir():
        print(
            f"WARNING: {recipes_path} not found; skipping inline icon sync",
            file=sys.stderr,
        )
        return []

    try:
        result = sync_icons(
            neurocontainers_path=neurocontainers_path,
            icons_dir=icons_dir,
            apps_json_path=Path(apps_json_path),
        )
    except Exception as exc:  # noqa: BLE001 - a bad recipe icon must not block apps.json
        print(f"WARNING: inline icon sync failed: {exc}", file=sys.stderr)
        return []

    for written in result.written_icons:
        print(f"Synced icon: {written}")
    for build_file in result.unsupported_icons:
        print(f"WARNING: unsupported icon data URI in {build_file}", file=sys.stderr)
    return [str(path) for path in result.written_icons]


def upsert_consolidated_pr(
    api_url: str,
    repo: str,
    owner: str,
    token: str,
    base_ref: str,
    head_branch: str,
    should_exist: bool,
    title: str,
    body: str,
) -> Optional[int]:
    existing = find_open_head_pr(api_url, repo, owner, head_branch, base_ref, token)

    if not should_exist:
        if existing:
            github_request(
                "PATCH",
                api_url,
                f"/repos/{repo}/pulls/{existing['number']}",
                token,
                payload={"state": "closed"},
            )
        return None

    if existing:
        github_request(
            "PATCH",
            api_url,
            f"/repos/{repo}/pulls/{existing['number']}",
            token,
            payload={"title": title, "body": body},
        )
        return int(existing["number"])

    created = github_request(
        "POST",
        api_url,
        f"/repos/{repo}/pulls",
        token,
        payload={
            "title": title,
            "head": head_branch,
            "base": base_ref,
            "body": body,
            "maintainer_can_modify": True,
        },
    )
    return int(created["number"])


def post_consolidated_diff_comment(
    api_url: str,
    repo: str,
    token: str,
    consolidated_pr_number: int,
    target_file: str,
    appsjson_diff: str,
) -> str:
    diff_for_comment, is_truncated = truncate_diff(appsjson_diff, MAX_PR_COMMENT_DIFF_CHARS)
    lines: List[str] = []
    lines.append(DIFF_COMMENT_MARKER)
    lines.append("### Proposed `apps.json` changes")
    lines.append("")
    lines.append(f"Latest queue consolidation diff for `{target_file}`:")
    lines.append("")
    if diff_for_comment:
        lines.append("```diff")
        lines.append(diff_for_comment)
        lines.append("```")
        if is_truncated:
            lines.append("")
            lines.append("_Diff truncated due to GitHub comment size limits._")
    else:
        lines.append("- No net changes.")

    comment_body = "\n".join(lines)

    existing_comments = github_paginated_get(
        api_url,
        f"/repos/{repo}/issues/{consolidated_pr_number}/comments",
        token,
    )
    for comment in reversed(existing_comments):
        body = comment.get("body") or ""
        if DIFF_COMMENT_MARKER in body:
            if body == comment_body:
                return "unchanged"
            comment_id = comment.get("id")
            if comment_id is not None:
                github_request(
                    "PATCH",
                    api_url,
                    f"/repos/{repo}/issues/comments/{comment_id}",
                    token,
                    payload={"body": comment_body},
                )
                return "updated"
            break

    github_request(
        "POST",
        api_url,
        f"/repos/{repo}/issues/{consolidated_pr_number}/comments",
        token,
        payload={"body": comment_body},
    )
    return "created"


def close_consolidated_source_prs(
    api_url: str,
    repo: str,
    token: str,
    source_prs: List[PullRequest],
) -> None:
    if not source_prs:
        return

    for pr in source_prs:
        github_request(
            "PATCH",
            api_url,
            f"/repos/{repo}/pulls/{pr.number}",
            token,
            payload={"state": "closed"},
        )


def merge_pull_request(
    api_url: str,
    repo: str,
    token: str,
    pr_number: int,
    merge_method: str,
    attempts: int = 5,
    retry_delay_seconds: float = 10.0,
) -> str:
    """Squash/merge a PR via REST, retrying while GitHub computes mergeability.

    The consolidated branch is force-pushed moments before this call, so the
    first attempts can fail with 405 (mergeability not yet computed) or 409
    (head moved). Anything still failing after the retries is reported as a
    status string rather than raised, so consolidation output is preserved.
    """
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            github_request(
                "PUT",
                api_url,
                f"/repos/{repo}/pulls/{pr_number}/merge",
                token,
                payload={"merge_method": merge_method},
            )
            return "merged"
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:  # noqa: BLE001 - diagnostics only
                pass
            last_error = f"HTTP {exc.code} {detail}".strip()
            if exc.code not in (405, 409) or attempt == attempts:
                break
            time.sleep(retry_delay_seconds)
    return f"failed ({last_error})"


def main() -> int:
    args = parse_args()
    token = require_token()
    # Merging with the default GITHUB_TOKEN would not trigger the push
    # workflows on main (update-neurocontainers etc.), so merges use a PAT
    # supplied via MERGE_TOKEN when available.
    merge_token = os.environ.get("MERGE_TOKEN") or token

    owner = args.repo.split("/", 1)[0]
    icons_prefix = f"{args.icons_dir.as_posix()}/"

    run_git(["fetch", "--no-tags", "origin", f"+refs/heads/{args.base_ref}:refs/remotes/origin/{args.base_ref}"])

    open_prs_raw = list_open_pull_requests(args.api_url, args.repo, token)

    relevant_prs: List[PullRequest] = []
    source_branch_covered_by_pr = False
    for pr in open_prs_raw:
        head_repo = (pr.get("head") or {}).get("repo") or {}
        head_ref = (pr.get("head") or {}).get("ref")
        if (
            head_repo.get("full_name", "").lower() == args.repo.lower()
            and head_ref == args.consolidated_branch
        ):
            continue

        files = list_pull_request_files(args.api_url, args.repo, int(pr["number"]), token)
        if args.target_file not in files:
            continue
        if (
            head_repo.get("full_name", "").lower() == args.repo.lower()
            and head_ref == args.source_branch
        ):
            source_branch_covered_by_pr = True
        relevant_prs.append(
            PullRequest(
                number=int(pr["number"]),
                created_at=pr["created_at"],
                title=pr["title"],
                html_url=pr["html_url"],
                files=files,
            )
        )

    relevant_prs.sort(key=lambda pr: (pr.created_at, pr.number))

    sources: List[QueueSource] = [
        QueueSource(
            created_at=pr.created_at,
            ref=f"refs/remotes/origin/pr/{pr.number}",
            label=f"#{pr.number}",
            title=pr.title,
            pr=pr,
        )
        for pr in relevant_prs
    ]

    # The neurocontainers release automation force-pushes a PR-less branch;
    # consume it as a queue source unless an open PR already tracks it.
    if args.source_branch and not source_branch_covered_by_pr:
        source_branch_ref = f"refs/remotes/origin/{args.source_branch}"
        fetched = run_git(
            [
                "fetch",
                "--no-tags",
                "origin",
                f"+refs/heads/{args.source_branch}:{source_branch_ref}",
            ],
            check=False,
        )
        if fetched.returncode == 0:
            commit_date = run_git(["log", "-1", "--format=%cI", source_branch_ref]).stdout.strip()
            sources.append(
                QueueSource(
                    created_at=commit_date,
                    ref=source_branch_ref,
                    label=f"branch `{args.source_branch}`",
                    title="neurocontainers release automation branch",
                )
            )

    sources.sort(key=lambda source: (parse_timestamp(source.created_at), source.label))

    base_ref = f"refs/remotes/origin/{args.base_ref}"
    base_payload = read_json_from_git(base_ref, args.target_file)
    existing_consolidated_pr = find_open_head_pr(
        args.api_url,
        args.repo,
        owner,
        args.consolidated_branch,
        args.base_ref,
        token,
    )

    existing_consolidated_payload: Dict[str, Any] = copy.deepcopy(base_payload)
    existing_consolidated_has_non_target_files = False
    if existing_consolidated_pr is not None:
        existing_pr_files = list_pull_request_files(
            args.api_url,
            args.repo,
            int(existing_consolidated_pr["number"]),
            token,
        )
        # Icons synced alongside apps.json are expected on the consolidated
        # branch and must not be treated as stray files that force a re-push.
        existing_consolidated_has_non_target_files = any(
            file_path != args.target_file and not file_path.startswith(icons_prefix)
            for file_path in existing_pr_files
        )

        consolidated_ref = f"refs/remotes/origin/{args.consolidated_branch}"
        fetch_consolidated = run_git(
            [
                "fetch",
                "--no-tags",
                "origin",
                f"+refs/heads/{args.consolidated_branch}:{consolidated_ref}",
            ],
            check=False,
        )
        if fetch_consolidated.returncode == 0:
            existing_consolidated_payload = read_json_from_git(consolidated_ref, args.target_file)

    # Route every apps.json update (even a lone source) through the
    # consolidated branch so its recipe icons can be synced inline before merge.
    consolidation_active = existing_consolidated_pr is not None or len(sources) >= 1

    if consolidation_active:
        consolidated_payload: Dict[str, Any] = copy.deepcopy(existing_consolidated_payload)
    else:
        consolidated_payload = copy.deepcopy(base_payload)

    source_changed_tools: Dict[str, List[str]] = {}
    final_winner_by_tool: Dict[str, str] = {}

    if consolidation_active:
        for source in sources:
            if source.pr is not None:
                run_git([
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"+refs/pull/{source.pr.number}/head:{source.ref}",
                ])

            merge_base = run_git(["merge-base", base_ref, source.ref]).stdout.strip()

            before_payload = read_json_from_git(merge_base, args.target_file)
            after_payload = read_json_from_git(source.ref, args.target_file)

            changed = changed_tools(before_payload, after_payload)
            source_changed_tools[source.label] = changed

            for tool in changed:
                if tool in after_payload:
                    consolidated_payload[tool] = copy.deepcopy(after_payload[tool])
                elif tool in consolidated_payload:
                    del consolidated_payload[tool]
                final_winner_by_tool[tool] = source.label

    write_json(args.target_file, consolidated_payload)

    consolidated_differs_from_base = consolidated_payload != base_payload
    consolidated_differs_from_existing = consolidated_payload != existing_consolidated_payload
    should_have_consolidated_pr = consolidation_active and consolidated_differs_from_base
    needs_branch_push = should_have_consolidated_pr and (
        existing_consolidated_pr is None
        or consolidated_differs_from_existing
        or existing_consolidated_has_non_target_files
    )

    consolidated_source_prs: List[PullRequest] = []
    if consolidation_active:
        for source in sources:
            pr = source.pr
            if pr is None:
                continue
            changed = source_changed_tools.get(source.label, [])
            if changed and pr.consolidatable(args.target_file, icons_prefix):
                consolidated_source_prs.append(pr)

    appsjson_diff = render_appsjson_diff(
        args.target_file,
        base_payload,
        consolidated_payload,
    )
    pr_body = build_consolidation_pr_body(
        args.base_ref,
        args.target_file,
        sources,
        final_winner_by_tool,
        consolidated_source_prs,
        should_have_consolidated_pr,
        appsjson_diff,
    )

    synced_icon_files: List[str] = []
    if needs_branch_push:
        if not args.skip_icon_sync:
            synced_icon_files = sync_consolidated_icons(
                neurocontainers_path=args.neurocontainers_path,
                icons_dir=args.icons_dir,
                apps_json_path=args.target_file,
            )
        stage_and_push_branch(
            base_ref=args.base_ref,
            head_branch=args.consolidated_branch,
            files=[args.target_file, *synced_icon_files],
            commit_message="Consolidate pending neurodesk/apps.json updates",
        )

    consolidated_pr_title = "Consolidate pending apps.json updates"
    consolidated_pr_number = upsert_consolidated_pr(
        api_url=args.api_url,
        repo=args.repo,
        owner=owner,
        token=token,
        base_ref=args.base_ref,
        head_branch=args.consolidated_branch,
        should_exist=should_have_consolidated_pr,
        title=consolidated_pr_title,
        body=pr_body,
    )

    diff_comment_status = "n/a"
    if consolidated_pr_number is not None:
        diff_comment_status = post_consolidated_diff_comment(
            api_url=args.api_url,
            repo=args.repo,
            token=token,
            consolidated_pr_number=consolidated_pr_number,
            target_file=args.target_file,
            appsjson_diff=appsjson_diff,
        )

    merge_status: Optional[str] = None
    if args.merge_consolidated and consolidated_pr_number is not None:
        merge_status = merge_pull_request(
            api_url=args.api_url,
            repo=args.repo,
            token=merge_token,
            pr_number=consolidated_pr_number,
            merge_method=args.merge_method,
        )
        if merge_status != "merged":
            print(
                f"WARNING: Failed to merge consolidated PR #{consolidated_pr_number}: {merge_status}",
                file=sys.stderr,
            )

    # Only close source PRs once their changes have actually landed on main:
    # either the consolidated PR merged in this run, or the queue output
    # already matches main. Otherwise leave them open for the next
    # scheduled merge run.
    changes_already_in_main = consolidation_active and not should_have_consolidated_pr
    sources_landed = changes_already_in_main or merge_status == "merged"
    if sources_landed:
        close_consolidated_source_prs(
            api_url=args.api_url,
            repo=args.repo,
            token=token,
            source_prs=consolidated_source_prs,
        )

    print("Consolidation summary:")
    print(f"- Queue sources: {[source.label for source in sources]}")
    print(f"- Consolidated tools: {len(final_winner_by_tool)}")
    print(
        "- Source PRs closed: "
        f"{[pr.number for pr in consolidated_source_prs] if sources_landed else '[] (pending merge)'}"
    )
    print(f"- Consolidated PR number: {consolidated_pr_number}")
    print(f"- Consolidated branch pushed: {'yes' if needs_branch_push else 'no'}")
    print(f"- Icons synced inline: {len(synced_icon_files)}")
    print(
        "- Existing consolidated PR had non-target files: "
        f"{'yes' if existing_consolidated_has_non_target_files else 'no'}"
    )
    print(f"- Consolidated PR diff comment: {diff_comment_status}")
    if args.merge_consolidated:
        print(f"- Consolidated PR merge: {merge_status or 'n/a'}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
