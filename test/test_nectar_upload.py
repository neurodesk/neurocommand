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


@pytest.mark.parametrize("reject_segment", [False, True])
@pytest.mark.parametrize("payload", [b"ab", b"abcdefghijkl"])
def test_nectar_upload_with_incomplete_segment_listings(
    tmp_path, reject_segment, payload
):
    objects = {}
    manifests = []
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
            if path in objects:
                self.respond(200, objects[path])
            else:
                # Newly uploaded segments are absent from container listings.
                self.respond(200, b"[]", **{"Content-Type": "application/json"})

        def do_PUT(self):
            url = urlsplit(self.path)
            path = unquote(url.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if path == "/v1/AUTH_test/neurodesk":
                self.respond(201)
                return
            if "multipart-manifest" in parse_qs(url.query):
                manifest = json.loads(body)
                manifests.append(manifest)
                for segment in manifest:
                    data = objects["/v1/AUTH_test" + segment["path"]]
                    assert len(data) == segment["size_bytes"]
                    assert hashlib.md5(data).hexdigest() == segment["etag"]
                objects[path] = b"".join(
                    objects["/v1/AUTH_test" + segment["path"]] for segment in manifest
                )
            else:
                if reject_segment:
                    self.respond(400)
                    return
                objects[path] = body
            self.respond(201, **{"Etag": hashlib.md5(body).hexdigest()})

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SwiftHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    env = {key: value for key, value in os.environ.items() if not key.startswith("OS_")}
    env.update(
        OS_STORAGE_URL=base + "/v1/AUTH_test",
        OS_AUTH_TOKEN="local-test-token",
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    )
    script = f"""
source {shlex.quote(str(SCRIPT))}
IMAGE_HOME={shlex.quote(str(tmp_path))}
NECTAR_BASE_URL={shlex.quote(base + "/v1/AUTH_test/neurodesk")}
AWS_BASE_URL={shlex.quote(base + "/aws")}
SWIFT_SEGMENT_SIZE=4
SWIFT_UPLOAD_RETRIES=1
URL_CHECK_RETRIES=1
rclone() {{ return 99; }}
ensure_released {shlex.quote(image.stem)}
"""
    try:
        result = subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30
        )
        if not reject_segment:
            with urlopen(base + "/v1/AUTH_test/neurodesk/" + image.name) as response:
                assert response.read() == payload
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

    target = "/v1/AUTH_test/neurodesk/" + image.name
    if reject_segment:
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
    assert image.read_bytes() == payload


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
        "upload --use-slo --segment-size 1073741824 --segment-container neurodesk "
        "--object-name image.simg neurodesk /cache/image.simg"
    )
    if application_credentials:
        expected = "--os-auth-type v3applicationcredential " + expected
    assert arguments == [expected, expected]
    assert "test-secret" not in result.stdout + result.stderr + calls.read_text()
