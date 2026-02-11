#!/usr/bin/env python3
"""Consolidate open neurodesk/apps.json PRs into a single queue PR.

Rules implemented:
- Process open PRs that touch neurodesk/apps.json in created_at order.
- Compute each PR's changed tools using merge-base(main, pr_head).
- Apply those tool changes to a consolidated apps.json snapshot.
- Later PRs overwrite earlier PRs for the same tool.
- Close fully superseded PRs that only modify neurodesk/apps.json.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class PullRequest:
    number: int
    created_at: str
    title: str
    html_url: str
    files: List[str]

    @property
    def apps_only(self) -> bool:
        return all(path == "neurodesk/apps.json" for path in self.files)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--graphql-url", default=os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql"))
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--target-file", default="neurodesk/apps.json")
    parser.add_argument("--consolidated-branch", default="bot/appsjson-consolidated")
    parser.add_argument("--report-file", default=".github/appsjson-consolidation-report.md")
    parser.add_argument("--enable-auto-merge", action="store_true")
    parser.add_argument(
        "--auto-merge-method",
        choices=["MERGE", "SQUASH", "REBASE"],
        default="SQUASH",
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


def github_graphql_request(graphql_url: str, token: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = Request(
        graphql_url,
        method="POST",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(req) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body) if body else {}
    errors = parsed.get("errors") or []
    if errors:
        messages = "; ".join(error.get("message", "unknown graphql error") for error in errors)
        raise RuntimeError(messages)
    return parsed.get("data", {})


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


def changed_tools(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    keys = set(before.keys()) | set(after.keys())
    changed = [tool for tool in keys if before.get(tool) != after.get(tool)]
    changed.sort()
    return changed


def write_json(path: str, payload: Dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")


def write_report(
    path: str,
    base_ref: str,
    target_file: str,
    relevant_prs: List[PullRequest],
    applied_tools: Dict[str, int],
    superseded_prs: List[PullRequest],
    should_have_consolidated_pr: bool,
) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines: List[str] = []
    lines.append("# apps.json Consolidation Report")
    lines.append("")
    lines.append(f"Generated at: {generated_at}")
    lines.append(f"Base branch: `{base_ref}`")
    lines.append(f"Target file: `{target_file}`")
    lines.append("")

    if relevant_prs:
        lines.append("## PR Processing Order")
        for pr in relevant_prs:
            lines.append(f"- #{pr.number} ({pr.created_at}): {pr.title}")
    else:
        lines.append("## PR Processing Order")
        lines.append("- No open pull requests currently modify `neurodesk/apps.json`.")

    lines.append("")
    lines.append("## Final Tool Winners")
    if applied_tools:
        for tool in sorted(applied_tools.keys()):
            lines.append(f"- `{tool}` -> #{applied_tools[tool]}")
    else:
        lines.append("- No tool-level changes were applied.")

    lines.append("")
    lines.append("## Superseded PRs")
    if superseded_prs:
        for pr in superseded_prs:
            lines.append(f"- #{pr.number}: {pr.title}")
    else:
        lines.append("- None")

    lines.append("")
    lines.append("## Consolidated PR State")
    if should_have_consolidated_pr:
        lines.append("- A consolidated PR is required because queue output differs from `main`.")
    else:
        lines.append("- No consolidated PR is required (queue output matches `main`).")

    report = "\n".join(lines) + "\n"
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    return report


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
                "POST",
                api_url,
                f"/repos/{repo}/issues/{existing['number']}/comments",
                token,
                payload={
                    "body": (
                        "Closing this queue PR because there are currently no net pending "
                        "`neurodesk/apps.json` changes to merge."
                    )
                },
            )
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


def close_superseded_prs(
    api_url: str,
    repo: str,
    token: str,
    superseded_prs: List[PullRequest],
    consolidated_pr_number: Optional[int],
) -> None:
    if not superseded_prs:
        return

    for pr in superseded_prs:
        message = (
            "This PR's `neurodesk/apps.json` changes are fully superseded by newer queue entries "
            "and were consolidated into "
        )
        if consolidated_pr_number is not None:
            message += f"#{consolidated_pr_number}. "
        else:
            message += "the latest queue snapshot. "
        message += "Closing to keep `apps.json` updates linear and deterministic."

        github_request(
            "POST",
            api_url,
            f"/repos/{repo}/issues/{pr.number}/comments",
            token,
            payload={"body": message},
        )
        github_request(
            "PATCH",
            api_url,
            f"/repos/{repo}/pulls/{pr.number}",
            token,
            payload={"state": "closed"},
        )


def enable_pull_request_auto_merge(
    graphql_url: str,
    owner: str,
    repo_name: str,
    token: str,
    pr_number: int,
    merge_method: str,
) -> str:
    lookup_query = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      autoMergeRequest {
        enabledAt
      }
    }
  }
}
"""

    lookup = github_graphql_request(
        graphql_url,
        token,
        lookup_query,
        {"owner": owner, "name": repo_name, "number": pr_number},
    )
    pr_data = ((lookup.get("repository") or {}).get("pullRequest")) or {}
    pr_id = pr_data.get("id")
    if not pr_id:
        raise RuntimeError(f"could not resolve PR node id for #{pr_number}")

    if pr_data.get("autoMergeRequest") is not None:
        return "already enabled"

    mutation = """
mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(input: {
    pullRequestId: $pullRequestId
    mergeMethod: $mergeMethod
  }) {
    pullRequest {
      number
    }
  }
}
"""
    github_graphql_request(
        graphql_url,
        token,
        mutation,
        {"pullRequestId": pr_id, "mergeMethod": merge_method},
    )
    return "enabled"


def main() -> int:
    args = parse_args()
    token = require_token()

    owner, repo_name = args.repo.split("/", 1)

    run_git(["fetch", "--no-tags", "origin", f"+refs/heads/{args.base_ref}:refs/remotes/origin/{args.base_ref}"])

    open_prs_raw = list_open_pull_requests(args.api_url, args.repo, token)

    relevant_prs: List[PullRequest] = []
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

    base_ref = f"refs/remotes/origin/{args.base_ref}"
    base_payload = read_json_from_git(base_ref, args.target_file)
    consolidated_payload: Dict[str, Any] = copy.deepcopy(base_payload)

    pr_changed_tools: Dict[int, List[str]] = {}
    final_winner_by_tool: Dict[str, int] = {}

    for pr in relevant_prs:
        run_git([
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/pull/{pr.number}/head:refs/remotes/origin/pr/{pr.number}",
        ])

        pr_ref = f"refs/remotes/origin/pr/{pr.number}"
        merge_base = run_git(["merge-base", base_ref, pr_ref]).stdout.strip()

        before_payload = read_json_from_git(merge_base, args.target_file)
        after_payload = read_json_from_git(pr_ref, args.target_file)

        changed = changed_tools(before_payload, after_payload)
        pr_changed_tools[pr.number] = changed

        for tool in changed:
            if tool in after_payload:
                consolidated_payload[tool] = copy.deepcopy(after_payload[tool])
            elif tool in consolidated_payload:
                del consolidated_payload[tool]
            final_winner_by_tool[tool] = pr.number

    write_json(args.target_file, consolidated_payload)

    consolidated_differs_from_base = consolidated_payload != base_payload
    should_have_consolidated_pr = bool(relevant_prs) and consolidated_differs_from_base

    superseded_prs: List[PullRequest] = []
    for pr in relevant_prs:
        changed = pr_changed_tools.get(pr.number, [])
        if not changed:
            continue
        won_any_tool = any(final_winner_by_tool.get(tool) == pr.number for tool in changed)
        if (not won_any_tool) and pr.apps_only:
            superseded_prs.append(pr)

    report_body = write_report(
        args.report_file,
        args.base_ref,
        args.target_file,
        relevant_prs,
        final_winner_by_tool,
        superseded_prs,
        should_have_consolidated_pr,
    )

    if should_have_consolidated_pr:
        stage_and_push_branch(
            base_ref=args.base_ref,
            head_branch=args.consolidated_branch,
            files=[args.target_file, args.report_file],
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
        body=report_body,
    )

    auto_merge_status: Optional[str] = None
    if args.enable_auto_merge and consolidated_pr_number is not None:
        try:
            auto_merge_result = enable_pull_request_auto_merge(
                graphql_url=args.graphql_url,
                owner=owner,
                repo_name=repo_name,
                token=token,
                pr_number=consolidated_pr_number,
                merge_method=args.auto_merge_method,
            )
            auto_merge_status = f"{auto_merge_result} ({args.auto_merge_method})"
        except Exception as exc:
            auto_merge_status = f"failed ({exc})"
            print(
                f"WARNING: Failed to enable auto-merge for PR #{consolidated_pr_number}: {exc}",
                file=sys.stderr,
            )

    close_superseded_prs(
        api_url=args.api_url,
        repo=args.repo,
        token=token,
        superseded_prs=superseded_prs,
        consolidated_pr_number=consolidated_pr_number,
    )

    print("Consolidation summary:")
    print(f"- Relevant PRs: {[pr.number for pr in relevant_prs]}")
    print(f"- Consolidated tools: {len(final_winner_by_tool)}")
    print(f"- Superseded PRs closed: {[pr.number for pr in superseded_prs]}")
    print(f"- Consolidated PR number: {consolidated_pr_number}")
    if args.enable_auto_merge:
        print(f"- Consolidated PR auto-merge: {auto_merge_status or 'n/a'}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
