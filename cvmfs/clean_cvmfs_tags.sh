#!/bin/bash

# --- Configuration ---
REPO="neurodesk.ardc.edu.au"
DAYS=30
# ---------------------

# Calculate the epoch time for the cutoff
CUTOFF_EPOCH=$(date -d "${DAYS} days ago" +%s)

echo "Scanning for custom tags in $REPO older than $DAYS days..."
echo "--------------------------------------------------------"

# Process the machine-readable tag list (-x)
# Format (CVMFS >= 2.10): Name Hash Timestamp Revision Description
# Format (CVMFS < 2.10):  Name Hash Channel Timestamp Revision Description
sudo cvmfs_server tag -l -x "$REPO" | while read -r line; do
    # Extract the tag name (1st column)
    TAG_NAME=$(echo "$line" | awk '{print $1}')
    
    # Safely skip headers, empty lines, or CVMFS's auto-generated generic tags
    # (Since generic tags are handled by CVMFS_AUTO_TAG_TIMESPAN)
    if [[ -z "$TAG_NAME" || "$TAG_NAME" == "Name" || "$TAG_NAME" == generic-* ]]; then
        continue
    fi
    
    # Extract the timestamp, accounting for different CVMFS version output formats
    COL3=$(echo "$line" | awk '{print $3}')
    if [[ "$COL3" =~ ^[0-9]{9,}$ ]]; then
        TAG_TIME=$COL3
    else
        TAG_TIME=$(echo "$line" | awk '{print $4}')
    fi

    # If we successfully parsed a timestamp, compare it against our cutoff
    if [[ "$TAG_TIME" =~ ^[0-9]+$ ]] && [ "$TAG_TIME" -lt "$CUTOFF_EPOCH" ]; then
        READABLE_DATE=$(date -d @"$TAG_TIME" "+%Y-%m-%d %H:%M:%S")
        echo "[MATCH] Tag '$TAG_NAME' from $READABLE_DATE is older than $DAYS days."
        
        # TODO: To actually delete the tags, uncomment the line below:
        sudo cvmfs_server tag -r "$TAG_NAME" "$REPO"
    fi
done

# echo "--------------------------------------------------------"
# echo "Done. (Note: This was a dry run. Edit the script to uncomment the deletion command when you are ready.)"