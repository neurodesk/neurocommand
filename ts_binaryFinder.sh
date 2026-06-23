#!/usr/bin/env bash
IFS=':'; \
for i in $DEPLOY_PATH; \
   do find "$i" -maxdepth 1 -executable -type f -exec basename {} \;; 
   find "$i" -maxdepth 1 -executable -type l -exec basename {} \;;
   done > commands_raw.txt

for i in $DEPLOY_BINS; \
   do echo "$i"; done >> commands_raw.txt

# if $DEBUG
#    export DEPLOY_ENV_FSLDIR="BASEPATH/opt/fsl-5.0.2"
#    export DEPLOY_ENV_SPMMCRCMD="BASEPATH/opt/spm12/run_spm12.sh BASEPATH/opt/mcr/v97/ script"
# fi


# This is what I ran to get an initial list of system commands for exclusion:
# IFS=':'; \
# for i in $PATH; \
#    do find "$i" -maxdepth 1 -executable -type f -exec basename {} \;; done > system_commands_raw.txt

# Find binaries in FSL image:
# comm -12 <( sort commands_raw.txt ) <( sort system_commands_raw.txt ) > overlap.txt


# Remove system applications, because they cause problems:
grep -vxf ts_binaryFinderExcludes.txt commands_raw.txt > commands.txt

env | grep '^DEPLOY_ENV_' > env.txt
grep_status=$?
if [[ "$grep_status" -gt 1 ]]; then
   exit "$grep_status"
fi
