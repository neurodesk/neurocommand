import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
import requests
import yaml


SCRIPT = Path(__file__).resolve().parents[1] / ".github/workflows/publish-doi.py"
WORKFLOW = SCRIPT.with_name("generate-doi.yml")


@pytest.fixture
def publisher(monkeypatch):
    spec = importlib.util.spec_from_file_location("publish_doi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "requests", Mock())
    module.requests.exceptions = requests.exceptions
    module.requests.adapters = requests.adapters
    monkeypatch.setattr(module.time, "sleep", Mock())
    return module


def response(payload=None, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = payload
    result.text = json.dumps(payload)
    result.content = yaml.safe_dump(payload).encode()
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP {status}", response=result
        )
    return result


def recipe_responses(publisher, copyright):
    publisher.requests.get.side_effect = [
        response({"download_url": "https://example.org/build.yaml"}),
        response({"copyright": copyright}),
    ]


def publication_responses(publisher):
    publisher.requests.get.return_value = response({"id": "mit"})
    publisher.requests.head.return_value = response()
    publisher.requests.head.return_value.headers = {"Content-Length": "10"}
    publisher.requests.post.side_effect = [
        response({"id": 123, "links": {"bucket": "https://zenodo.org/bucket"}}),
        response({"doi_url": "https://doi.org/10.1234/example"}),
    ]
    publisher.requests.put.return_value = response()
    publisher.requests.Session.return_value.put.return_value = response()
    publisher.requests.delete.return_value = response(status=204)


@pytest.mark.parametrize("license_id", ["custom", "LicenseRef-Example"])
def test_unknown_license_fails_before_creating_or_uploading(publisher, license_id):
    publication_responses(publisher)
    publisher.requests.get.return_value = response({}, status=404)
    with pytest.raises(ValueError, match="other-open.*other-closed"):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {
                "id": license_id,
                "title": license_id,
                "url": "https://example.org/LICENSE",
            },
        )
    publisher.requests.post.assert_not_called()
    publisher.requests.Session.assert_not_called()


def test_typo_reports_vocabulary_suggestions_without_substituting_them(publisher):
    publication_responses(publisher)
    publisher.requests.get.side_effect = [
        response({}, status=404),
        response({"hits": {"hits": [{"id": "apache-2.0"}, {"id": "mit"}]}}),
    ]
    with pytest.raises(ValueError, match="Closest Zenodo IDs: apache-2.0"):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "apache-2.00"},
        )
    publisher.requests.post.assert_not_called()


def test_standard_license_is_validated_against_current_vocabulary(publisher):
    publisher.requests.get.return_value = response({"id": "mit"})
    assert publisher.validate_zenodo_license({"id": "MIT"}) == "mit"
    publisher.requests.get.assert_called_once_with(
        "https://zenodo.org/api/vocabularies/licenses/mit", timeout=30
    )


@pytest.mark.parametrize("url", [None, "", "relative/path", "https://", 42])
def test_custom_license_requires_absolute_url_before_network_calls(publisher, url):
    with pytest.raises(ValueError, match="requires a URL"):
        publisher.validate_zenodo_license({"id": "other-closed", "url": url})
    publisher.requests.get.assert_not_called()


@pytest.mark.parametrize("copyright", [None, [], [{"name": "Unknown license"}]])
def test_missing_or_unclassified_recipe_license_is_not_assumed_open(
    publisher, copyright
):
    recipe_responses(publisher, copyright)
    with pytest.raises(ValueError, match="license"):
        publisher.get_license("tool_1.0", "secret")


def test_recipe_fetch_failure_is_not_replaced_with_a_license(publisher):
    publisher.requests.get.return_value = response({}, status=403)
    with pytest.raises(requests.HTTPError):
        publisher.get_license("tool_1.0", "secret")


def test_explicit_custom_license_keeps_name_and_url(publisher):
    recipe_responses(
        publisher,
        [
            {
                "license": "other-closed",
                "name": "Research only <license>",
                "url": "https://example.org/LICENSE",
            }
        ],
    )
    assert publisher.get_license("tool_1.0", "secret") == {
        "id": "other-closed",
        "title": "Research only <license>",
        "url": "https://example.org/LICENSE",
    }


def test_license_details_are_in_description_and_id_is_sent_to_zenodo(publisher):
    publication_responses(publisher)
    publisher.requests.get.return_value = response({"id": "other-closed"})
    doi = publisher.upload_container(
        "https://example.org/tool.simg",
        "tool_1.0",
        "secret",
        {
            "id": "other-closed",
            "title": "Research only <license>",
            "url": "https://example.org/LICENSE",
        },
    )
    metadata = publisher.requests.put.call_args.kwargs["json"]["metadata"]
    assert metadata["license"] == "other-closed"
    assert "https://example.org/LICENSE" in metadata["description"]
    assert "Research only &lt;license&gt;" in metadata["description"]
    assert doi == "https://doi.org/10.1234/example"
    publisher.requests.delete.assert_not_called()


@pytest.mark.parametrize("stage", ["metadata", "publish", "upload"])
def test_http_failure_removes_draft_and_reports_original_error(publisher, stage):
    publication_responses(publisher)
    rejected = response({"message": "Rejected metadata"}, status=400)
    if stage == "metadata":
        publisher.requests.put.return_value = rejected
    elif stage == "publish":
        publisher.requests.post.side_effect = [
            response({"id": 123, "links": {"bucket": "https://zenodo.org/bucket"}}),
            rejected,
        ]
    else:
        publisher.requests.Session.return_value.put.return_value = rejected
    with pytest.raises(Exception, match="HTTP 400"):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "MIT"},
        )
    publisher.requests.delete.assert_called_once()
    assert publisher.requests.delete.call_args.args[0].endswith("/123")


def test_failed_creation_does_not_delete_an_unknown_deposition(publisher):
    publication_responses(publisher)
    publisher.requests.post.side_effect = None
    publisher.requests.post.return_value = response({}, status=401)
    with pytest.raises(Exception, match="HTTP 401"):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "MIT"},
        )
    publisher.requests.delete.assert_not_called()


def test_cleanup_failure_does_not_hide_original_error(publisher, capsys):
    publication_responses(publisher)
    publisher.requests.put.return_value = response({}, status=400)
    publisher.requests.delete.return_value = response({}, status=503)
    with pytest.raises(Exception, match="HTTP 400"):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "MIT"},
        )
    assert "123" in capsys.readouterr().err


def test_metadata_rejection_does_not_transfer_image(publisher):
    publication_responses(publisher)
    publisher.requests.put.return_value = response({}, status=400)
    with pytest.raises(requests.HTTPError):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "MIT"},
        )
    publisher.requests.Session.assert_not_called()


def test_exhausted_network_retries_remove_draft(publisher):
    publication_responses(publisher)
    publisher.requests.Session.return_value.put.side_effect = requests.Timeout(
        "timeout"
    )
    with pytest.raises(requests.Timeout):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "MIT"},
        )
    assert publisher.requests.Session.return_value.put.call_count == 3
    publisher.requests.delete.assert_called_once()


def test_transient_upload_failure_retries_and_publishes(publisher):
    publication_responses(publisher)
    publisher.requests.Session.return_value.put.side_effect = [
        requests.Timeout("timeout"),
        response(),
    ]
    assert (
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "MIT"},
        )
        == "https://doi.org/10.1234/example"
    )
    publisher.requests.delete.assert_not_called()


def test_upload_streams_source_bytes_to_zenodo(publisher):
    publication_responses(publisher)
    source = response()
    source.iter_content.return_value = iter([b"container", b" image"])
    publisher.requests.get.side_effect = [response({"id": "mit"}), source]
    uploaded = bytearray()

    def receive_image(url, *, data, **kwargs):
        while chunk := data.read():
            uploaded.extend(chunk)
        return response()

    publisher.requests.Session.return_value.put.side_effect = receive_image
    publisher.upload_container(
        "https://example.org/tool.simg",
        "tool_1.0",
        "secret",
        {"id": "MIT"},
    )
    assert uploaded == b"container image"
    source.close.assert_called_once()


def test_doi_lookup_failure_does_not_delete_published_record(publisher):
    publication_responses(publisher)
    publisher.requests.post.side_effect = [
        response({"id": 123, "links": {"bucket": "https://zenodo.org/bucket"}}),
        response({}),
    ]
    publisher.requests.get.side_effect = [
        response({"id": "mit"}),
        response({}, status=503),
    ]
    with pytest.raises(requests.HTTPError):
        publisher.upload_container(
            "https://example.org/tool.simg",
            "tool_1.0",
            "secret",
            {"id": "MIT"},
        )
    publisher.requests.delete.assert_not_called()


@pytest.mark.parametrize("failed_app, expected_code", [("bad_1.0", 1), ("", 0)])
def test_batch_attempts_remaining_apps_and_reports_any_failure(
    tmp_path, failed_app, expected_code
):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    script = workflow["jobs"]["publish-doi"]["steps"][-1]["run"]
    script = script.replace(
        "${{ toJson(matrix.app_batches.apps) }}", '["bad_1.0", "good_1.0"]'
    )
    script = script.replace("${{ secrets.ZENODO_TOKEN }}", "test-zenodo")
    script = script.replace("${{ secrets.GITHUB_TOKEN }}", "test-github")
    commands = {
        "curl": "exit 0\n",
        "jq": "printf '%s\\n' bad_1.0 good_1.0\n",
        "python3": (
            'echo "$*" >> "$CALL_LOG"\n'
            'case "$*" in *"--container_name=$FAIL_APP "*) exit 1 ;; esac\n'
            "echo https://doi.org/10.1234/example\n"
        ),
    }
    for name, contents in commands.items():
        command = tmp_path / name
        command.write_text("#!/bin/sh\n" + contents)
        command.chmod(0o755)
    log = tmp_path / "calls"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CALL_LOG": str(log),
            "FAIL_APP": failed_app,
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == expected_code, result.stdout + result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert "--container_name=bad_1.0" in calls[0]
    assert "--container_name=good_1.0" in calls[1]
