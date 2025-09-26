import requests
import json
import argparse
import os
import sys
import yaml
import time
from tqdm import tqdm
from urllib3.exceptions import ProtocolError
from requests.exceptions import ChunkedEncodingError, ConnectionError

ZENODO_API_URL = "https://sandbox.zenodo.org/api/deposit/depositions"

def get_license(container_name, gh_token):
    """
    Get the license from copyright field in YAML file in the container.
    """
    # Get yaml recipe using github API
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + gh_token,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Get the recipe name from the container name
    recipe_name = container_name.split("_")[0]
    url = f" https://api.github.com/repos/Neurodesk/neurocontainers/contents/recipes/{recipe_name}/build.yaml"
    try:
        if 'matlab' in container_name.lower():
            print("MATLAB container, skipping license check")
            return {
                'id': "other-closed",
                'title': "MATLAB",
                'url': "https://www.mathworks.com/products/matlab.html"
            }
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        download_url = response.json().get("download_url")
        if not download_url:
            print("No download_url found in GitHub API response")
            return ""
        download_url_response = requests.get(download_url)
        download_url_response.raise_for_status()
        content = download_url_response.content.decode("utf-8")
        tinyrange_config = yaml.safe_load(content)
        copyrights = tinyrange_config.get('copyright')
        print("Copyright field", copyrights)
        if not copyrights or not isinstance(copyrights, list):
            print("No copyright field found in the recipe")
            return ""
        # Return the first license found in the copyright field
        license_info = copyrights[0]
        license = license_info.get('license')
        license_url = license_info.get('url')
        if license:
            return {
                'id': license.lower(),
                'title': license,
                'url': license_url
            }
        else:
            return {
                'id': "other-open",
                'title': license_info.get('name') or "Custom",
                'url': license_url
            }
    except Exception as e:
        print(f"WARNING: Failed to get recipe or parse license: {e}")
        return {
                'id': "other-at",
                'title': "Custom"
            }

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
            if hasattr(self, 'resp') and self.resp:
                self.resp.close()
            raise e
        
    def __len__(self):
        return self.total_size
    
    def close(self):
        """Clean up resources"""
        if hasattr(self, 'resp') and self.resp:
            self.resp.close()
            self.resp = None
            self.iterator = None

def upload_container(container_url, container_name, token, license):
    """
    Upload simg to Zenodo and return the DOI URL.
    """
    headers = {"Content-Type": "application/json"}
    params = {'access_token': token}

    # Get file size if possible
    total_size = None
    head = requests.head(container_url)
    total_size = int(head.headers.get("Content-Length", 0))

    print(f"Uploading {container_name} of size {total_size} to Zenodo...")
    # Create a new deposition
    try:
        r = requests.post(f'{ZENODO_API_URL}',
                        params=params,
                        json={},
                        headers=headers)
        deposition_id = r.json()['id']
        bucket_url = r.json()["links"]["bucket"]
    except Exception as e:
        r = requests.delete(f'{ZENODO_API_URL}/{deposition_id}',
                    headers={'Authorization': f'Bearer {token}'})
        raise Exception(f"Failed to create deposition: {e}. Cleanup response: {r.status_code} {r.text}")
    
    # Upload the simg container to bucket in the created deposition
    # The target URL is a combination of the bucket link with the desired filename
    # seperated by a slash.
    # print("Uploading container to Zenodo...", container_url)
    max_retries = 3
    retry_delay = 60  # seconds
    
    for attempt in range(max_retries):
        remote_file = None
        session = None
        try:
            print(f"Upload attempt {attempt + 1}/{max_retries}")
            with tqdm(total=total_size, unit="B", unit_scale=True, desc=os.path.basename(container_url)) as pbar:
                remote_file = RemoteStream(container_url, total_size, pbar)
                
                # Configure requests session with longer timeouts and connection pooling
                session = requests.Session()
                session.mount('https://', requests.adapters.HTTPAdapter(
                    max_retries=0,  # We handle retries manually
                    pool_connections=1,
                    pool_maxsize=1
                ))
                
                r = session.put(
                    f"{bucket_url}/{os.path.basename(container_url)}", # bucket is a flat structure, can't include subfolders in it
                    data=remote_file,  # Stream the file directly
                    params=params,
                    timeout=(30, 300),  # (connect_timeout, read_timeout) in seconds
                    stream=True
                )
                r.raise_for_status()  # Ensure the upload was successful
                
                # If we get here, upload was successful
                print(f"Upload successful on attempt {attempt + 1}")
                break
                
        except (ProtocolError, ChunkedEncodingError, ConnectionError, requests.exceptions.Timeout) as e:
            print(f"Network error on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                r = requests.delete(f'{ZENODO_API_URL}/{deposition_id}',
                    headers={'Authorization': f'Bearer {token}'})
                raise Exception(f"Failed to upload container after {max_retries} attempts. Last error: {e}")
        except Exception as e:
            # For non-network errors, don't retry
            r = requests.delete(f'{ZENODO_API_URL}/{deposition_id}',
                headers={'Authorization': f'Bearer {token}'})
            raise Exception(f"Failed to upload container (non-network error): {e}")
        finally:
            # Clean up resources
            if remote_file:
                remote_file.close()
            if session:
                session.close()

    # print("Upload", r.json())

    # Update the metadata
    try:
        data = {
            'metadata': {
                'title': container_name,
                'upload_type': 'software',
                'description': container_name,
                'creators': [{'name': 'Neurodesk',
                            'affiliation': 'University of Queensland'}]
            }
        }
        if license:
            data['metadata']['license'] = license
            print("Updating metadata", data)
            r = requests.put(f'{ZENODO_API_URL}/{deposition_id}',
                            params=params, data=json.dumps(data),
                            headers=headers)
        else:
            r = requests.put(f'{ZENODO_API_URL}/{deposition_id}',
                    params=params, data=json.dumps(data),
                    headers=headers)
    except Exception as e:
        r = requests.delete(f'{ZENODO_API_URL}/{deposition_id}',
            headers={'Authorization': f'Bearer {token}'})
        raise Exception(f"Failed to update metadata: {e}")

    # Publish the deposition
    try:
        r = requests.post(f'{ZENODO_API_URL}/{deposition_id}/actions/publish',
                          params=params)
        print("Publish", r.json())
    except Exception as e:
        r = requests.delete(f'{ZENODO_API_URL}/{deposition_id}',
            headers={'Authorization': f'Bearer {token}'})
        raise Exception(f"Failed to publish deposition: {e}")

    # Get the DOI from the deposition
    try:
        r = requests.get(f'{ZENODO_API_URL}/{deposition_id}',
                params=params,
                headers=headers)
    except Exception as e:
        raise Exception(f"Failed to get DOI from deposition: {e}")
    doi_url = r.json()["doi_url"]
    return doi_url

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog="Upload container to Zenodo",
    )
    
    parser.add_argument("--container_filepath", type=str, required=True, help="Container file to upload to Zenodo")
    parser.add_argument("--container_name", type=str, required=True, help="Container name")
    parser.add_argument("--zenodo_token", type=str, required=True, help="Zenodo token")
    parser.add_argument("--gh_token", type=str, required=True, help="GitHub token to access the recipe")
    args = parser.parse_args()

    try:
        license = get_license(args.container_name, args.gh_token)
        doi_url = upload_container(args.container_filepath, args.container_name, args.zenodo_token, license)
        print(doi_url)
    except Exception as e:
        print(f"ERROR: Script failed with exception: {e}", file=sys.stderr)
        sys.exit(1)