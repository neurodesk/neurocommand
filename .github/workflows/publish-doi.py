import argparse
from difflib import get_close_matches
from html import escape
import os
import sys
import time
from urllib.parse import quote, urlparse

import requests
import yaml
from tqdm import tqdm
from urllib3.exceptions import ProtocolError
from requests.exceptions import ChunkedEncodingError, ConnectionError

ZENODO_API_URL = "https://zenodo.org/api/deposit/depositions"
ZENODO_LICENSES_URL = "https://zenodo.org/api/vocabularies/licenses"
CUSTOM_LICENSES = {"other-open", "other-closed"}
LICENSE_HELP = (
    "Set copyright[].license to a supported license identifier. "
    "For a custom license, explicitly choose other-open or other-closed "
    "and provide its name and URL in the recipe."
)


def get_license(container_name: str, gh_token: str) -> dict[str, str | None]:
    """Read the primary license from the current Neurocontainers recipe."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + gh_token,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    recipe_name = container_name.split("_")[0]
    url = (
        "https://api.github.com/repos/neurodesk/neurocontainers/contents/"
        f"recipes/{recipe_name}/build.yaml"
    )
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    download_url = response.json().get("download_url")
    if not download_url:
        raise ValueError(f"No recipe download URL for {recipe_name}")
    response = requests.get(download_url, timeout=30)
    response.raise_for_status()
    recipe = yaml.safe_load(response.content)
    copyrights = recipe.get("copyright") if isinstance(recipe, dict) else None
    if not isinstance(copyrights, list) or not copyrights:
        raise ValueError(f"No license metadata for {recipe_name}. {LICENSE_HELP}")
    license_info = copyrights[0]
    if not isinstance(license_info, dict):
        raise ValueError(f"Invalid license metadata for {recipe_name}. {LICENSE_HELP}")
    license_id = license_info.get("license")
    if not isinstance(license_id, str) or not license_id.strip():
        raise ValueError(f"No license identifier for {recipe_name}. {LICENSE_HELP}")
    return {
        "id": license_id.strip().lower(),
        "title": license_info.get("name") or license_id,
        "url": license_info.get("url"),
    }


def validate_zenodo_license(license_info: dict[str, str | None]) -> str:
    """Reject unsupported IDs before creating a draft or transferring an image."""
    if not isinstance(license_info, dict) or not isinstance(
        license_info.get("id"), str
    ):
        raise ValueError(f"Missing license identifier. {LICENSE_HELP}")
    license_id = license_info["id"].strip().lower()
    if not license_id:
        raise ValueError(f"Missing license identifier. {LICENSE_HELP}")
    license_url = license_info.get("url")
    if license_id in CUSTOM_LICENSES and (
        not isinstance(license_url, str)
        or urlparse(license_url).scheme not in {"http", "https"}
        or not urlparse(license_url).netloc
    ):
        raise ValueError(
            f"Custom license '{license_id}' requires a URL. {LICENSE_HELP}"
        )
    response = requests.get(
        f"{ZENODO_LICENSES_URL}/{quote(license_id, safe='')}", timeout=30
    )
    if response.status_code == 404:
        suggestions = []
        try:
            vocabulary = requests.get(
                f"{ZENODO_LICENSES_URL}/", params={"size": 1000}, timeout=30
            )
            vocabulary.raise_for_status()
            payload = vocabulary.json()
            entries = (
                payload
                if isinstance(payload, list)
                else payload.get("hits", {}).get("hits", [])
            )
            identifiers = [
                entry.get("id") or entry.get("metadata", {}).get("id")
                for entry in entries
            ]
            suggestions = get_close_matches(
                license_id, [item for item in identifiers if isinstance(item, str)], n=3
            )
        except (
            requests.exceptions.RequestException,
            ValueError,
            TypeError,
            AttributeError,
        ):
            pass
        hint = f" Closest Zenodo IDs: {', '.join(suggestions)}." if suggestions else ""
        raise ValueError(
            f"Unsupported Zenodo license '{license_id}'.{hint} {LICENSE_HELP}"
        )
    response.raise_for_status()
    return license_id


CHUNK_SIZE = 1024 * 1024 * 10  # 10MB


class RemoteStream:
    def __init__(self, url, total_size, pbar):
        self.url = url
        self.total_size = total_size
        self.pbar = pbar
        self.bytes_read = 0
        self.resp = None
        self.iterator = None

    def _initialize_stream(self):
        """Initialize or reinitialize the stream"""
        if self.resp:
            self.resp.close()  # Close any existing connection
        self.resp = requests.get(self.url, stream=True, timeout=(30, 300))
        self.resp.raise_for_status()
        self.iterator = self.resp.iter_content(chunk_size=CHUNK_SIZE)

    def read(self, size=None):
        # Lazy initialization - only create connection when first read is attempted
        if self.iterator is None:
            self._initialize_stream()

        try:
            chunk = next(self.iterator)
            self.bytes_read += len(chunk)
            self.pbar.update(len(chunk))
            return chunk
        except StopIteration:
            return b""  # end of stream
        except Exception as e:
            # Close the current response to free resources
            if hasattr(self, "resp") and self.resp:
                self.resp.close()
            raise e

    def __len__(self):
        return self.total_size

    def close(self):
        """Clean up resources"""
        if hasattr(self, "resp") and self.resp:
            self.resp.close()
            self.resp = None
            self.iterator = None


def upload_file(
    container_url: str, bucket_url: str, headers: dict[str, str], total_size: int
) -> None:
    """Stream an image, retrying interrupted transfers into the same draft."""
    max_retries = 3
    retry_delay = 60  # seconds

    for attempt in range(max_retries):
        remote_file = None
        session = None
        try:
            print(f"Upload attempt {attempt + 1}/{max_retries}", file=sys.stderr)
            with tqdm(
                total=total_size,
                unit="B",
                unit_scale=True,
                desc=os.path.basename(container_url),
            ) as pbar:
                remote_file = RemoteStream(container_url, total_size, pbar)

                # Configure requests session with longer timeouts and connection pooling
                session = requests.Session()
                session.mount(
                    "https://",
                    requests.adapters.HTTPAdapter(
                        max_retries=0,  # We handle retries manually
                        pool_connections=1,
                        pool_maxsize=1,
                    ),
                )

                r = session.put(
                    f"{bucket_url}/{os.path.basename(container_url)}",
                    data=remote_file,
                    headers=headers,
                    timeout=(30, 300),
                    stream=True,
                )
                r.raise_for_status()  # Ensure the upload was successful

                return

        except (
            ProtocolError,
            ChunkedEncodingError,
            ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            print(f"Network error on attempt {attempt + 1}: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...", file=sys.stderr)
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                raise
        finally:
            # Clean up resources
            if remote_file is not None:
                remote_file.close()
            if session is not None:
                session.close()


def upload_container(
    container_url: str, container_name: str, token: str, license: dict[str, str | None]
) -> str:
    """Validate metadata, upload an image, and publish its DOI."""
    license_id = validate_zenodo_license(license)
    headers = {"Authorization": f"Bearer {token}"}
    description = escape(container_name)
    if license.get("title"):
        description += f"<p>License: {escape(license['title'])}</p>"
    if license.get("url"):
        description += f"<p>License URL: {escape(license['url'])}</p>"
    data = {
        "metadata": {
            "title": container_name,
            "upload_type": "software",
            "description": description,
            "license": license_id,
            "creators": [
                {"name": "Neurodesk", "affiliation": "University of Queensland"}
            ],
        }
    }
    head = requests.head(container_url, timeout=30)
    head.raise_for_status()
    total_size = int(head.headers.get("Content-Length", 0))
    deposition_id = None
    published = False
    try:
        response = requests.post(ZENODO_API_URL, headers=headers, json={}, timeout=30)
        response.raise_for_status()
        deposition = response.json()
        deposition_id = deposition["id"]
        bucket_url = deposition["links"]["bucket"]
        response = requests.put(
            f"{ZENODO_API_URL}/{deposition_id}",
            headers=headers,
            json=data,
            timeout=30,
        )
        response.raise_for_status()
        upload_file(container_url, bucket_url, headers, total_size)
        response = requests.post(
            f"{ZENODO_API_URL}/{deposition_id}/actions/publish",
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        published = True
        record = response.json()
        if not record.get("doi_url"):
            response = requests.get(
                f"{ZENODO_API_URL}/{deposition_id}", headers=headers, timeout=30
            )
            response.raise_for_status()
            record = response.json()
        return record["doi_url"]
    except Exception:
        if deposition_id is not None and not published:
            try:
                cleanup = requests.delete(
                    f"{ZENODO_API_URL}/{deposition_id}", headers=headers, timeout=30
                )
                cleanup.raise_for_status()
            except requests.exceptions.RequestException as error:
                print(
                    f"WARNING: Failed to delete draft {deposition_id}: {error}",
                    file=sys.stderr,
                )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="Upload container to Zenodo",
    )

    parser.add_argument(
        "--container_filepath",
        type=str,
        required=True,
        help="Container file to upload to Zenodo",
    )
    parser.add_argument(
        "--container_name", type=str, required=True, help="Container name"
    )
    parser.add_argument("--zenodo_token", type=str, required=True, help="Zenodo token")
    parser.add_argument(
        "--gh_token", type=str, required=True, help="GitHub token to access the recipe"
    )
    args = parser.parse_args()

    try:
        license = get_license(args.container_name, args.gh_token)
        doi_url = upload_container(
            args.container_filepath, args.container_name, args.zenodo_token, license
        )
        print(doi_url)
    except Exception as e:
        print(f"ERROR: Script failed with exception: {e}", file=sys.stderr)
        sys.exit(1)
