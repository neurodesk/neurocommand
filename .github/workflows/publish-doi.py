import requests
import json
import argparse
import os
import yaml

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
        license = license_info.get('license') or license_info.get('name')
        license_url = license_info.get('url')
        if license:
            return {
                'id': license.lower(),
                'title': license,
                'url': license_url
            }
        else:
            return ""
    except Exception as e:
        print(f"Failed to get recipe or parse license: {e}")
        return ""

def stream_download(url, chunk_size=1024*1024):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk

def upload_container(container_url, container_name, token, license):
    """
    Upload simg to Zenodo and return the DOI URL.
    """
    headers = {"Content-Type": "application/json"}
    params = {'access_token': token}

    # Create a new deposition
    r = requests.post('https://sandbox.zenodo.org/api/deposit/depositions',
                    params=params,
                    json={},
                    headers=headers)
    deposition_id = r.json()['id']
    bucket_url = r.json()["links"]["bucket"]

    print("licenses", license)
    # Update the metadata
    data = {
        'metadata': {
            'title': container_name,
            'upload_type': 'software',
            'description': container_name,
            'creators': [{
                'name': 'Neurodesk',
                'affiliation': 'University of Queensland'
            }]
        }
    }
    if license:
        data['metadata']['license'] = license
        print("Updating metadata", data)
    r = requests.put('https://sandbox.zenodo.org/api/deposit/depositions/%s' % deposition_id,
            params=params, data=json.dumps(data),
            headers=headers)
    print("Update metadata", r.json())
    # Stream upload to Zenodo bucket
    upload_url = f"{bucket_url}/{os.path.basename(container_url)}"
    with requests.Session() as session:
        with session.put(
            upload_url,
            data=stream_download(container_url),
            params=params,
        ) as upload_response:
            upload_response.raise_for_status()
    

    # Publish the deposition
    r = requests.post('https://sandbox.zenodo.org/api/deposit/depositions/%s/actions/publish' % deposition_id,
                      params=params )
    print("Publish", r.json())

    # Get the DOI from the deposition
    r = requests.get('https://sandbox.zenodo.org/api/deposit/depositions/%s' % deposition_id,
            params=params,
            headers=headers)
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

    license = get_license(args.container_name, args.gh_token)
    doi_url = upload_container(args.container_filepath, args.container_name, args.zenodo_token, license)
    print(doi_url)