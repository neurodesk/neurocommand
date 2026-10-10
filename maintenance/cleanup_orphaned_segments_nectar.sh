#!/bin/bash

# This cleanup was necessary because rclone in version 1.54 wasn't deleting the segments correctly. The latest version of rclone fixes this problem and this is how we cleanup up the orphaned segments:

# The initialization file is supplied by the deployment environment.
# shellcheck disable=SC1090
source ~/swift_setup.sh
swift list neurodesk_segments > neurodesk_segments.txt
swift list neurodesk > legitimate_objects.txt

# check if the object is in the legitimate_objects.txt
while IFS= read -r line
do
  name=$(echo "$line" | cut -d'/' -f1)
#   echo "looking for $name"
  if grep -q "$name" legitimate_objects.txt; then
    # echo "$line is a legitimate object"
    continue
  else
    echo "$line is an orphaned object"
    # swift delete neurodesk_segments $line
  fi
done < neurodesk_segments.txt
