#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Set


def run_command(cmd: List[str]) -> None:
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"[ERROR] Command failed ({' '.join(cmd)}): {exc}")


def generate_log(log_path: Path) -> None:
    print("[DEBUG] Generating log file from neurodesk/apps.json ...")
    run_command(["python3", "neurodesk/write_log.py"])
    if not log_path.exists():
        raise SystemExit(f"[ERROR] {log_path} was not created by neurodesk/write_log.py")


def normalize_log(log_path: Path) -> None:
    lines = []
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.replace("[", "").replace("]", "").strip()
        if line:
            lines.append(line)

    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print("[DEBUG] Normalized log file:")
    print(log_path.read_text(encoding="utf-8"), end="")


def parse_iso8601(timestamp: str) -> Optional[datetime]:
    if not timestamp or not isinstance(timestamp, str):
        return None

    # rclone lsjson may emit nanosecond precision and different UTC offset forms.
    # Python 3.8's datetime.fromisoformat cannot parse >6 fractional digits.
    ts = timestamp.strip()
    if not ts:
        return None

    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"

    match = re.match(
        r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
        r"(?P<fraction>\.\d+)?"
        r"(?P<offset>[+-]\d{2}:\d{2}|[+-]\d{4})?$",
        ts,
    )
    if match:
        base = match.group("base")
        fraction = match.group("fraction") or ""
        offset = match.group("offset") or "+00:00"

        if fraction:
            fraction = "." + fraction[1:7].ljust(6, "0")

        if re.fullmatch(r"[+-]\d{4}", offset):
            offset = f"{offset[:3]}:{offset[3:]}"

        normalized = f"{base}{fraction}{offset}"
    else:
        normalized = ts

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_expected_keys(log_path: Path) -> Set[str]:
    if not log_path.exists():
        raise SystemExit("[ERROR] log.txt not found; cannot determine expected containers")

    expected_keys = set()
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        image_builddate = line.split()[0]
        key = image_builddate if image_builddate.endswith(".simg") else f"{image_builddate}.simg"
        expected_keys.add(key)

    return expected_keys


def list_nectar_objects(remote_root: str) -> List[dict]:
    try:
        lsjson = subprocess.check_output(
            ["rclone", "lsjson", "--recursive", "--files-only", remote_root],
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"[ERROR] Failed to list objects in {remote_root}: {exc}")
    return json.loads(lsjson)


def list_s3_objects(bucket: str) -> List[dict]:
    objects = []
    continuation_token = None

    while True:
        cmd = [
            "aws",
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--output",
            "json",
            "--no-cli-pager",
        ]
        if continuation_token:
            cmd.extend(["--continuation-token", continuation_token])

        try:
            page_raw = subprocess.check_output(cmd, text=True)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"[ERROR] Failed listing AWS objects for bucket {bucket}: {exc}")

        page = json.loads(page_raw)
        objects.extend(page.get("Contents", []))

        if not page.get("IsTruncated"):
            break

        continuation_token = page.get("NextContinuationToken")
        if not continuation_token:
            break

    return objects


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find stale container images in Nectar and S3 using log.txt membership."
    )
    parser.add_argument("--s3-bucket", required=True, help="S3 bucket name")
    parser.add_argument("--remote-root", default="nectar:/neurodesk/", help="Nectar remote path")
    parser.add_argument("--log-path", default="log.txt", help="Path to log file")
    parser.add_argument("--retention-days", type=int, default=30, help="Retention period in days")
    parser.add_argument(
        "--skip-log-generation",
        action="store_true",
        help="Use existing log.txt and skip running neurodesk/write_log.py",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Only print what would be deleted (default)",
    )
    parser.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually delete stale objects",
    )
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.retention_days)
    log_path = Path(args.log_path)
    if not args.skip_log_generation:
        generate_log(log_path)
        normalize_log(log_path)
    expected_keys = load_expected_keys(log_path)

    print(f"[DEBUG] Expected container count from log: {len(expected_keys)}")
    print(f"[DEBUG] Retention window for stale deletions: {args.retention_days} day(s)")
    if args.dry_run:
        print("[DEBUG] Deletion mode: DRY-RUN (no objects will be deleted)")
    else:
        print("[DEBUG] Deletion mode: LIVE")

    nectar_to_delete = []
    for obj in list_nectar_objects(args.remote_root):
        key = obj.get("Path", "")
        if not key.endswith(".simg") or key in expected_keys:
            continue

        mod_time = parse_iso8601(obj.get("ModTime", ""))
        if mod_time is None:
            print(f"[WARNING] Skipping Nectar deletion for {key}: missing/invalid ModTime")
            continue

        if mod_time >= cutoff:
            print(
                f"[DEBUG] Keeping Nectar object not in log but younger than {args.retention_days} days: "
                f"{key} (ModTime={mod_time.isoformat()})"
            )
            continue

        nectar_to_delete.append(key)

    for key in sorted(nectar_to_delete):
        remote_obj = args.remote_root.rstrip("/") + "/" + key
        if args.dry_run:
            print(f"[DRY-RUN] Would delete Nectar object not in log: {remote_obj}")
        else:
            print(f"[DEBUG] Deleting Nectar object not in log: {remote_obj}")
            subprocess.run(["rclone", "deletefile", remote_obj], check=True)

    if args.dry_run:
        print(f"[DRY-RUN] Would delete {len(nectar_to_delete)} stale object(s) from Nectar")
    else:
        print(f"[DEBUG] Deleted {len(nectar_to_delete)} stale object(s) from Nectar")

    aws_to_delete = []
    for obj in list_s3_objects(args.s3_bucket):
        key = obj.get("Key", "")
        if not key.endswith(".simg") or key in expected_keys:
            continue

        last_modified = parse_iso8601(obj.get("LastModified", ""))
        if last_modified is None:
            print(f"[WARNING] Skipping AWS deletion for {key}: missing/invalid LastModified")
            continue

        if last_modified >= cutoff:
            print(
                f"[DEBUG] Keeping AWS object not in log but younger than {args.retention_days} days: "
                f"{key} (LastModified={last_modified.isoformat()})"
            )
            continue

        aws_to_delete.append(key)

    for key in sorted(aws_to_delete):
        if args.dry_run:
            print(f"[DRY-RUN] Would delete AWS object not in log: s3://{args.s3_bucket}/{key}")
        else:
            print(f"[DEBUG] Deleting AWS object not in log: s3://{args.s3_bucket}/{key}")
            subprocess.run(
                [
                    "aws",
                    "s3api",
                    "delete-object",
                    "--bucket",
                    args.s3_bucket,
                    "--key",
                    key,
                    "--no-cli-pager",
                ],
                check=True,
            )

    if args.dry_run:
        print(
            f"[DRY-RUN] Would delete {len(aws_to_delete)} stale object(s) from AWS S3 bucket {args.s3_bucket}"
        )
    else:
        print(f"[DEBUG] Deleted {len(aws_to_delete)} stale object(s) from AWS S3 bucket {args.s3_bucket}")


if __name__ == "__main__":
    main()
