#!/usr/bin/env bash
set -e

S3_BUCKET="neurocontainers"

#creating logfile with available containers
python3 neurodesk/write_log.py
pip3 install requests


# remove empty lines
sed -i '/^$/d' log.txt

# remove square brackets
sed -i 's/[][]//g' log.txt

# remove spaces around
sed -i -e 's/^[ \t]*//' -e 's/[ \t]*$//' log.txt

echo "[debug] logfile:"
cat log.txt
echo "[debug] logfile is at: $PWD"


mapfile -t arr < log.txt
for LINE in "${arr[@]}";
do
    echo "LINE: $LINE"
    IMAGENAME_BUILDDATE="$(cut -d' ' -f1 <<< "${LINE}")"
    echo "IMAGENAME_BUILDDATE: $IMAGENAME_BUILDDATE"

    IMAGENAME="$(cut -d'_' -f1,2 <<< "${IMAGENAME_BUILDDATE}")"
    BUILDDATE="$(cut -d'_' -f3 <<< "${IMAGENAME_BUILDDATE}")"
    echo "[DEBUG] IMAGENAME: $IMAGENAME"
    echo "[DEBUG] BUILDDATE: $BUILDDATE"
    OBJECT_KEY="${IMAGENAME_BUILDDATE}.simg"

    if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/${OBJECT_KEY}"; then
        echo "[DEBUG] ${OBJECT_KEY} exists in nectar cloud"
        echo "[DEBUG] refresh timestamp to show it's still in use"
        rclone touch "nectar:/neurodesk/${OBJECT_KEY}"

        # Refresh S3 object's LastModified timestamp (used by lifecycle expiration)
        if aws s3api head-object --bucket "${S3_BUCKET}" --key "${OBJECT_KEY}" > /dev/null 2>&1; then
            aws s3 cp "s3://${S3_BUCKET}/${OBJECT_KEY}" "s3://${S3_BUCKET}/${OBJECT_KEY}" \
              --copy-props default \
              --only-show-errors \
              --no-progress > /dev/null
            echo "[DEBUG] Refreshed AWS timestamp for ${OBJECT_KEY}"
        else
            echo "[DEBUG] ${OBJECT_KEY} not found in AWS S3; skipping timestamp refresh"
        fi
    fi 
done < log.txt


echo "[DEBUG] Dry-run: listing containers that would be deleted because they are not in log.txt ..."
python3 - "${S3_BUCKET}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta


s3_bucket = sys.argv[1]
remote_root = "nectar:/neurodesk/"
log_path = Path("log.txt")
retention_days = 30
cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
dry_run = True

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

print(f"[DEBUG] Expected container count from log: {len(expected_keys)}")
print(f"[DEBUG] Retention window for stale deletions: {retention_days} day(s)")
if dry_run:
    print("[DEBUG] Deletion mode: DRY-RUN (no objects will be deleted)")
else:
    print("[DEBUG] Deletion mode: LIVE")


def parse_iso8601(timestamp: str):
    if not timestamp:
        return None
    normalized = timestamp.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

try:
    lsjson = subprocess.check_output(
        ["rclone", "lsjson", "--recursive", "--files-only", remote_root],
        text=True,
    )
except subprocess.CalledProcessError as exc:
    raise SystemExit(f"[ERROR] Failed to list objects in {remote_root}: {exc}")

nectar_objects = json.loads(lsjson)
nectar_to_delete = []
for obj in nectar_objects:
    key = obj.get("Path", "")
    if not key.endswith(".simg") or key in expected_keys:
        continue

    mod_time = parse_iso8601(obj.get("ModTime", ""))
    if mod_time is None:
        print(f"[WARNING] Skipping Nectar deletion for {key}: missing/invalid ModTime")
        continue

    if mod_time >= cutoff:
        print(
            f"[DEBUG] Keeping Nectar object not in log but younger than {retention_days} days: "
            f"{key} (ModTime={mod_time.isoformat()})"
        )
        continue

    nectar_to_delete.append(key)

nectar_to_delete.sort()

for key in nectar_to_delete:
    remote_obj = remote_root.rstrip("/") + "/" + key
    if dry_run:
        print(f"[DRY-RUN] Would delete Nectar object not in log: {remote_obj}")
    else:
        print(f"[DEBUG] Deleting Nectar object not in log: {remote_obj}")
        subprocess.run(["rclone", "deletefile", remote_obj], check=True)

if dry_run:
    print(f"[DRY-RUN] Would delete {len(nectar_to_delete)} stale object(s) from Nectar")
else:
    print(f"[DEBUG] Deleted {len(nectar_to_delete)} stale object(s) from Nectar")


def list_s3_objects(bucket: str) -> list:
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


aws_to_delete = []
for obj in list_s3_objects(s3_bucket):
    key = obj.get("Key", "")
    if not key.endswith(".simg") or key in expected_keys:
        continue

    last_modified = parse_iso8601(obj.get("LastModified", ""))
    if last_modified is None:
        print(f"[WARNING] Skipping AWS deletion for {key}: missing/invalid LastModified")
        continue

    if last_modified >= cutoff:
        print(
            f"[DEBUG] Keeping AWS object not in log but younger than {retention_days} days: "
            f"{key} (LastModified={last_modified.isoformat()})"
        )
        continue

    aws_to_delete.append(key)

aws_to_delete.sort()

for key in aws_to_delete:
    if dry_run:
        print(f"[DRY-RUN] Would delete AWS object not in log: s3://{s3_bucket}/{key}")
    else:
        print(f"[DEBUG] Deleting AWS object not in log: s3://{s3_bucket}/{key}")
        subprocess.run(
            [
                "aws",
                "s3api",
                "delete-object",
                "--bucket",
                s3_bucket,
                "--key",
                key,
                "--no-cli-pager",
            ],
            check=True,
        )

if dry_run:
    print(f"[DRY-RUN] Would delete {len(aws_to_delete)} stale object(s) from AWS S3 bucket {s3_bucket}")
else:
    print(f"[DEBUG] Deleted {len(aws_to_delete)} stale object(s) from AWS S3 bucket {s3_bucket}")
PY
