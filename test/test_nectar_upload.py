import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import urlopen

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1] / ".github/workflows/upload_containers_simg.sh"
)


@pytest.mark.parametrize(
    ("payload", "failure"),
    [
        (b"ab", None),
        (b"abcdefghijkl", None),
        (b"ab", "segment"),
        (b"abcdefghijkl", "segment"),
        (b"abcdefghijkl", "manifest"),
        (b"abcdefghijkl", "first_manifest"),
    ],
)
@pytest.mark.parametrize("auth_mode", ["token", "application", "password"])
def test_nectar_upload_publishes_only_complete_objects(
    tmp_path, payload, failure, auth_mode
):
    objects = {}
    manifests = []
    manifest_attempts = []
    segment_uploads = []
    auth_requests = []
    image = tmp_path / "relion_5.1.1_20261015.simg"
    image.write_bytes(payload)

    class SwiftHandler(BaseHTTPRequestHandler):
        def respond(self, status, body=b"", **headers):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            path = unquote(urlsplit(self.path).path)
            if path == "/aws/" + image.name:
                self.respond(200, payload)
            elif path in objects:
                self.respond(200, objects[path])
            else:
                self.respond(404)

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path.rstrip("/") == "/v3":
                self.respond(
                    200,
                    json.dumps(
                        {
                            "version": {
                                "id": "v3.0",
                                "status": "stable",
                                "links": [{"rel": "self", "href": base + "/v3/"}],
                            }
                        }
                    ).encode(),
                    **{"Content-Type": "application/json"},
                )
            elif path in objects:
                self.respond(200, objects[path])
            else:
                # Newly uploaded segments are absent from container listings.
                self.respond(200, b"[]", **{"Content-Type": "application/json"})

        def do_POST(self):
            assert self.path == "/v3/auth/tokens"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            auth_requests.append(request)
            token = {
                "methods": request["auth"]["identity"]["methods"],
                "expires_at": "2099-01-01T00:00:00Z",
                "issued_at": "2026-10-17T00:00:00Z",
                "user": {
                    "id": "test-user",
                    "name": "test-user",
                    "domain": {"id": "default", "name": "Default"},
                },
                "project": {
                    "id": "test-project",
                    "name": "test-project",
                    "domain": {"id": "default", "name": "Default"},
                },
                "catalog": [
                    {
                        "id": "test-swift",
                        "name": "swift",
                        "type": "object-store",
                        "endpoints": [
                            {
                                "id": "test-endpoint",
                                "interface": "public",
                                "region": "RegionOne",
                                "url": base + "/v1/AUTH_test",
                            }
                        ],
                    }
                ],
            }
            self.respond(
                201,
                json.dumps({"token": token}).encode(),
                **{
                    "Content-Type": "application/json",
                    "X-Subject-Token": "local-test-token",
                },
            )

        def do_PUT(self):
            url = urlsplit(self.path)
            path = unquote(url.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if path == "/v1/AUTH_test/neurodesk":
                self.respond(201)
                return
            if "multipart-manifest" in parse_qs(url.query):
                manifest = json.loads(body)
                manifest_attempts.append(manifest)
                if failure == "manifest" or (
                    failure == "first_manifest" and len(manifest_attempts) == 1
                ):
                    self.respond(400)
                    return
                manifests.append(manifest)
                for segment in manifest:
                    data = objects["/v1/AUTH_test" + segment["path"]]
                    assert len(data) == segment["size_bytes"]
                    assert hashlib.md5(data).hexdigest() == segment["etag"]
                objects[path] = b"".join(
                    objects["/v1/AUTH_test" + segment["path"]] for segment in manifest
                )
            else:
                if failure == "segment":
                    self.respond(400)
                    return
                objects[path] = body
                segment_uploads.append(path)
            self.respond(201, **{"Etag": hashlib.md5(body).hexdigest()})

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SwiftHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    env = {key: value for key, value in os.environ.items() if not key.startswith("OS_")}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    if auth_mode == "token":
        env.update(
            OS_STORAGE_URL=base + "/v1/AUTH_test", OS_AUTH_TOKEN="local-test-token"
        )
    else:
        env.update(
            OS_AUTH_URL=base + "/v3",
            OS_AUTH_VERSION="3",
            OS_IDENTITY_API_VERSION="3",
            OS_PROJECT_ID="test-project",
            OS_PROJECT_NAME="",
            OS_USERNAME="test-user",
            OS_PASSWORD="test-password",
            OS_USER_DOMAIN_NAME="Default",
        )
        if auth_mode == "application":
            env.update(
                OS_APPLICATION_CREDENTIAL_ID="test-id",
                OS_APPLICATION_CREDENTIAL_SECRET="test-secret",
            )
    script = f"""
source {shlex.quote(str(SCRIPT))}
IMAGE_HOME={shlex.quote(str(tmp_path))}
NECTAR_BASE_URL={shlex.quote(base + "/v1/AUTH_test/neurodesk")}
AWS_BASE_URL={shlex.quote(base + "/aws")}
SWIFT_SEGMENT_SIZE=4
SWIFT_UPLOAD_RETRIES={2 if failure in ("manifest", "first_manifest") else 1}
SWIFT_UPLOAD_RETRY_DELAY=0
URL_CHECK_RETRIES=1
rclone() {{ return 99; }}
ensure_released {shlex.quote(image.stem)}
"""
    try:
        result = subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30
        )
        if failure in (None, "first_manifest"):
            assert result.returncode == 0, result.stdout + result.stderr
            with urlopen(base + "/v1/AUTH_test/neurodesk/" + image.name) as response:
                assert response.read() == payload
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

    target = "/v1/AUTH_test/neurodesk/" + image.name
    if failure in ("segment", "manifest"):
        assert result.returncode != 0, result.stdout + result.stderr
        assert target not in objects
        assert not manifests
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert objects[target] == payload
        if len(payload) > 4:
            assert len(manifests) == 1
            assert [part["size_bytes"] for part in manifests[0]] == [4, 4, 4]
        else:
            assert not manifests
    if failure in ("manifest", "first_manifest"):
        assert len(manifest_attempts) == 2
        assert len(segment_uploads) == 6
        # Retries keep the cached file's mtime and overwrite the same segments.
        assert len(set(segment_uploads)) == 3
    assert image.read_bytes() == payload
    if auth_mode == "token":
        assert not auth_requests
    else:
        assert auth_requests
        identity = auth_requests[0]["auth"]["identity"]
        if auth_mode == "application":
            assert identity["methods"] == ["application_credential"]
            assert identity["application_credential"] == {
                "id": "test-id",
                "secret": "test-secret",
            }
        else:
            assert identity["methods"] == ["password"]
            assert identity["password"]["user"]["password"] == "test-password"


@pytest.mark.parametrize("application_credentials", [False, True])
def test_nectar_upload_authentication_and_retries(tmp_path, application_credentials):
    calls = tmp_path / "swift-calls"
    env = {key: value for key, value in os.environ.items() if not key.startswith("OS_")}
    if application_credentials:
        env.update(
            OS_APPLICATION_CREDENTIAL_ID="test-id",
            OS_APPLICATION_CREDENTIAL_SECRET="test-secret",
        )
    script = f"""
source {shlex.quote(str(SCRIPT))}
SWIFT_UPLOAD_RETRIES=2
SWIFT_UPLOAD_RETRY_DELAY=0
swift() {{
    printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
    return 7
}}
upload_nectar_image /cache/image.simg
"""
    result = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 7, result.stdout + result.stderr
    arguments = calls.read_text().splitlines()
    expected = (
        "--timeout 60 upload --use-slo --segment-size 1073741824 --segment-container neurodesk "
        "--object-name image.simg neurodesk /cache/image.simg"
    )
    if application_credentials:
        expected = "--os-auth-type v3applicationcredential " + expected
    assert arguments == [expected, expected]
    assert "test-secret" not in result.stdout + result.stderr + calls.read_text()


def test_nectar_credentials_are_checked_before_generating_inventory():
    script = f"""
source {shlex.quote(str(SCRIPT))}
SWIFT_UPLOAD_RETRIES=1
swift() {{ return 7; }}
python3() {{ echo unexpected-inventory-generation; return 99; }}
main
"""
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert "unexpected-inventory-generation" not in result.stdout


def test_existing_truncated_nectar_image_is_repaired(tmp_path):
    image = tmp_path / "relion_5.1.1_20261015.simg"
    image.write_bytes(b"abcdefghijkl")
    uploaded = tmp_path / "uploaded"
    script = f"""
source {shlex.quote(str(SCRIPT))}
IMAGE_HOME={shlex.quote(str(tmp_path))}
URL_CHECK_RETRIES=1
url_exists() {{ return 0; }}
url_size() {{
    if [[ "$1" == "$AWS_BASE_URL/"* || -f {shlex.quote(str(uploaded))} ]]; then
        echo 12
    else
        echo 8
    fi
}}
ensure_valid_local_image() {{ return 0; }}
upload_nectar_image() {{ touch {shlex.quote(str(uploaded))}; }}
rclone() {{ return 99; }}
process_container_line "relion_5.1.1_20261015 categories:imaging,"
"""
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert uploaded.exists()
    assert not image.exists()
