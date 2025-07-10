#!/usr/bin/env bash
set -e

IMAGE_HOME=/home/runner
IMAGENAME_BUILDDATE=dicomtools_1.0.0_20250204
echo "[DEBUG] ${IMAGENAME_BUILDDATE}.simg exists in temporary cache on nectar cloud"
curl --output "$IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg" "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/${IMAGENAME_BUILDDATE}.simg"
echo "Upload container $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg to Zenodo"
echo $(find $IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg)
export DOI_URL=$(python3 .github/workflows/publish-doi.py --container_filepath="$IMAGE_HOME/${IMAGENAME_BUILDDATE}.simg" --container_name=${IMAGENAME_BUILDDATE} --token=$1)
echo "DOI_URL: $DOI_URL"