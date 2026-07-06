#!/usr/bin/env bash
#Deploy script for singularity Containers "Transparent Singularity"
#Creates wrapper scripts for all executables in a container's $DEPLOY_PATH
# singularity needs to be available
# for downloading images from nectar it needs curl installed
#11/07/2018
#by Steffen Bollmann <Steffen.Bollmann@cai.uq.edu.au> & Tom Shaw <t.shaw@uq.edu.au>
# set -e

echo "[DEBUG] This is the run_transparent_singularity.sh script"

fail() {
   echo "[ERROR] run_transparent_singularity.sh: $*" >&2
   exit 2
}

download_container_from_nectar() {
   local fallback_container="$1"
   local nectar_url
   local nectar_urls=(
      "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/temporary-builds-new/"
      "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/"
   )

   if ! command -v curl >/dev/null 2>&1; then
      echo "[WARN] curl is not available; cannot use Nectar fallback for '${fallback_container}'." >&2
      return 1
   fi

   for nectar_url in "${nectar_urls[@]}"; do
      echo "checking Nectar fallback: ${nectar_url}${fallback_container}"
      if ! curl --output /dev/null --silent --head --fail "${nectar_url}${fallback_container}"; then
         continue
      fi

      echo "downloading from Nectar fallback: ${nectar_url}${fallback_container}"
      rm -f "$fallback_container"
      if curl --fail --location --retry 5 --retry-delay 10 \
            --output "$fallback_container" "${nectar_url}${fallback_container}"; then
         return 0
      fi
      rm -f "$fallback_container"
   done

   return 1
}

run_container_pull_with_fallback() {
   if [[ -z "${container_pull:-}" ]]; then
      echo "[ERROR] No retrieval command was selected for '${container}'." >&2
      return 1
   fi

   echo "running: $container_pull"
   if $container_pull; then
      return 0
   fi

   local pull_status=$?
   if [[ "$storage" = "quay-v2" ]] || [[ "$storage" = "ghcr-v2" ]]; then
      echo "[WARN] ORAS pull failed for '${container}'. Trying Nectar object-storage fallback." >&2
      if download_container_from_nectar "$container"; then
         return 0
      fi
      echo "[ERROR] ORAS pull failed with status ${pull_status}, and Nectar fallback did not retrieve '${container}'." >&2
   fi

   return "$pull_status"
}

export SINGULARITY_BINDPATH=$SINGULARITY_BINDPATH,$PWD

_script="$(readlink -f ${BASH_SOURCE[0]})" ## who am i? ##
_base="$(dirname $_script)" ## Delete last component from $_script ##

# echo "making sure this is not running in a symlinked directory (singularity bug)"
# echo "path: $_base"
cd $_base
_base=`pwd -P`
# echo "corrected path: $_base"

POSITIONAL=()
while [[ $# -gt 0 ]]
   do
   key="$1"

   case $key in
      -s|--storage)
      storage="$2"
      shift # past argument
      shift # past value
      ;;
      -c|--container)
      container="$2"
      shift # past argument
      shift # past value
      ;;
      -u|--unpack)
      unpack="$2"
      shift # past argument
      shift # past value
      ;;
      -o|--singularity-opts)
      singularity_opts="$2"
      shift # past argument
      shift # past value
      ;;
      --default)
      DEFAULT=YES
      shift # past argument
      ;;
      *)    # unknown option
      POSITIONAL+=("$1") # save it in an array for later
      shift # past argument
      ;;
   esac
done
set -- "${POSITIONAL[@]}" # restore positional parameters


if [[ -n $1 ]]; then
    container="$1"
   # e.g. export container=matlab_2024b_20250117
fi

if [ -z "$container" ]; then
      echo "-----------------------------------------------"
      echo "Select the container you would like to install:"
      echo "-----------------------------------------------"
      echo "singularity container list:"
      curl -s https://raw.githubusercontent.com/NeuroDesk/neurodesk/master/cvmfs/log.txt
      echo " "
      echo "-----------------------------------------------"
      echo "usage examples:"
      echo "./run_transparent_singularity.sh CONTAINERNAME"
      echo "./run_transparent_singularity.sh --container convert3d_1.0.0_20210104.simg --storage docker"
      echo "./run_transparent_singularity.sh convert3d_1.0.0_20210104.simg"
      echo "./run_transparent_singularity.sh convert3d_1.0.0_20210104 --unpack true --singularity-opts '--bind /cvmfs'"
      echo "-----------------------------------------------"
      exit
   else
      echo "-------------------------------------"
      echo "installing container ${container}"
      echo "-------------------------------------"


      # define mount points for this system
      echo "-------------------------------------"
      echo 'IMPORTANT: you need to set your system specific mount points in your .bashrc!: e.g. export SINGULARITY_BINDPATH="/opt,/data"'
      echo "-------------------------------------"
fi

containerName="$(cut -d'_' -f1 <<< ${container})"
echo "containerName: ${containerName}"

containerVersion="$(cut -d'_' -f2 <<< ${container})"
echo "containerVersion: ${containerVersion}"

containerDateAndFileEnding="$(cut -d'_' -f3 <<< ${container})"
containerDate="$(cut -d'.' -f1 <<< ${containerDateAndFileEnding})"
containerEnding="$(cut -d'.' -f2 <<< ${containerDateAndFileEnding})"

echo "containerDate: ${containerDate}"

# if no container extension is given, assume .simg
if [ "$containerEnding" = "$containerDate" ]; then
   containerEnding="simg"
   container=${containerName}_${containerVersion}_${containerDate}.${containerEnding}
fi
echo "containerEnding: ${containerEnding}"

if [[ -z "$containerName" ]] || [[ -z "$containerVersion" ]] || [[ ! "$containerDate" =~ ^[0-9]{8}$ ]]; then
   fail "Container name must match name_version_YYYYMMDD[.simg]; got '${container}'. Parsed build date was '${containerDate:-<empty>}'."
fi


# echo "checking for singularity ..."
qq=`which  singularity`
if [[  ${#qq} -lt 1 ]]; then
   echo "This script requires singularity or apptainer on your path. E.g. add 'module load singularity' to your .bashrc"
   echo "If you are root try again as normal user"
   exit 2
fi

# Select the container runtime for image pulls. Both apptainer and SingularityCE
# support oras:// and docker:// pulls, so use whichever is on the PATH (prefer apptainer).
if command -v apptainer >/dev/null 2>&1; then
   container_runtime="apptainer"
else
   container_runtime="singularity"
fi

echo "checking if $container exists in the cvmfs cache ..."
if  [[ -z "$CVMFS_DISABLE" ]] && [[ -d "/cvmfs/neurodesk.ardc.edu.au/containers/${containerName}_${containerVersion}_${containerDate}/${containerName}_${containerVersion}_${containerDate}.simg" ]]; then
   echo "$container exists in cvmfs"
   storage="cvmfs"
   container_pull="ln -s /cvmfs/neurodesk.ardc.edu.au/containers/${containerName}_${containerVersion}_${containerDate}/${containerName}_${containerVersion}_${containerDate}.simg $container"
else
   # Pull SIF artifact from quay.io via OCI 1.1 Referrers API
   echo "checking if $container is published as v2 OCI on Quay ..."
   ts_quay_repo="neurodesk/${containerName}"
   ts_docker_tag="${containerVersion}_${containerDate}"
   ts_sif_digest=""
   ts_sif_mediatype="application/vnd.sylabs.sif.layer.v1.sif"

   if command -v curl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
      ts_token=$(curl -sL \
         "https://quay.io/v2/auth?service=quay.io&scope=repository:${ts_quay_repo}:pull" 2>/dev/null \
         | jq -r '.token // empty')
      if [ -n "$ts_token" ] && [ "$ts_token" != "null" ]; then
         ts_docker_digest=$(curl -sIL -H "Authorization: Bearer $ts_token" \
            -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json" \
            "https://quay.io/v2/${ts_quay_repo}/manifests/${ts_docker_tag}" 2>/dev/null \
            | awk 'tolower($1)=="docker-content-digest:" {print $2}' | tr -d '\r')
         if [ -n "$ts_docker_digest" ]; then
            ts_sif_digest=$(curl -sL -H "Authorization: Bearer $ts_token" \
               "https://quay.io/v2/${ts_quay_repo}/referrers/${ts_docker_digest}?artifactType=${ts_sif_mediatype}" 2>/dev/null \
               | jq -r '.manifests[0].digest // empty')
         fi
      fi
   fi

   if [ -n "$ts_sif_digest" ] && [ "$ts_sif_digest" != "null" ]; then
      echo "  found v2 SIF on Quay: $ts_sif_digest"
      storage="quay-v2"
      container_pull="$container_runtime pull --name $container oras://quay.io/${ts_quay_repo}@${ts_sif_digest}"
   fi
fi

# Pull SIF artifact from ghcr.io via OCI Referrers (tag-schema fallback; GHCR has no native Referrers API)
if [ -z "$storage" ]; then
   echo "checking if $container is published as v2 OCI on GHCR ..."
   ts_ghcr_repo="neurodesk/${containerName}"
   ts_sif_digest=""
   if command -v curl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
      ts_token=$(curl -sL \
         "https://ghcr.io/token?service=ghcr.io&scope=repository:${ts_ghcr_repo}:pull" 2>/dev/null \
         | jq -r '.token // empty')
      if [ -n "$ts_token" ] && [ "$ts_token" != "null" ]; then
         ts_docker_digest=$(curl -sIL -H "Authorization: Bearer $ts_token" \
            -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.docker.distribution.manifest.list.v2+json" \
            "https://ghcr.io/v2/${ts_ghcr_repo}/manifests/${ts_docker_tag}" 2>/dev/null \
            | awk 'tolower($1)=="docker-content-digest:" {print $2}' | tr -d '\r')
         if [ -n "$ts_docker_digest" ]; then
            # GHCR exposes referrers via the OCI fallback tag: sha256-<digest>
            ts_referrers_tag=$(echo "$ts_docker_digest" | sed 's/:/-/')
            ts_sif_digest=$(curl -sL -H "Authorization: Bearer $ts_token" \
               -H "Accept: application/vnd.oci.image.index.v1+json" \
               "https://ghcr.io/v2/${ts_ghcr_repo}/manifests/${ts_referrers_tag}" 2>/dev/null \
               | jq -r --arg mt "$ts_sif_mediatype" '.manifests[]? | select(.artifactType==$mt) | .digest' | head -1)
         fi
      fi
   fi

   if [ -n "$ts_sif_digest" ] && [ "$ts_sif_digest" != "null" ]; then
      echo "  found v2 SIF on GHCR: $ts_sif_digest"
      storage="ghcr-v2"
      container_pull="$container_runtime pull --name $container oras://ghcr.io/${ts_ghcr_repo}@${ts_sif_digest}"
   fi
fi

if [ -z "$storage" ]; then
   echo "$container does not exist in cvmfs or v2 oras. Testing Nectar temporary Object storage next: "
   if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/temporary-builds-new/$container"; then      
      echo "$container exists in the temporary builds nectar cache"
      url_nectar="https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/temporary-builds-new/"
   fi

   echo "Testing standard Nectar Object storage next: "
   if curl --output /dev/null --silent --head --fail "https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/$container"; then
      echo "$container exists in the standard nectar object storage"
      url_nectar="https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/"
   fi

   echo "Testing temporary AWS S3 Object storage next: "
   if curl --output /dev/null --silent --head --fail "https://neurocontainers.s3.us-east-2.amazonaws.com/temporary-builds-new/$container"; then      
      echo "$container exists in the temporary builds cache"
      url_awss3="https://neurocontainers.s3.us-east-2.amazonaws.com/temporary-builds-new/"
   fi

   echo "Testing standard Object storage next: "
   if curl --output /dev/null --silent --head --fail "https://neurocontainers.s3.us-east-2.amazonaws.com/$container"; then
      echo "$container exists in the standard object storage"
      url_awss3="https://neurocontainers.s3.us-east-2.amazonaws.com/"
   fi

   if [[ -n "${url_awss3+x}" ]] || [[ -n "${url_nectar+x}" ]]; then
      # echo "check if aria2 is installed ..."
      qq=`which  aria2c`
      if [[  ${#qq} -lt 1 ]]; then
          echo "aria2 is not installed. Defaulting to curl."
         
          urls=()
          if [[ -n "${url_awss3+x}" ]]; then
             urls+=("$url_awss3")
          fi
          if [[ -n "${url_nectar+x}" ]]; then
             urls+=("$url_nectar")
          fi
          declare -a speeds   
              
          echo "testing which server is fastest."
          for url in "${urls[@]}";          
          do  
             echo testing $url
             if avg_speed=$(curl -s -w %{time_total}\\n -o /dev/null "$url")
                then          
                   echo ResponseTime: "$avg_speed"
                   speeds+=($avg_speed)     
             fi  # of speed test            
          done # end of URL for loop
              
          count=0             
          for speed in "${speeds[@]}";      
          do 
             #echo comparing $speed with $avg_speed
             #echo currently fastest server is: $url
             #echo count: $count
             if (( $(echo "$speed < $avg_speed" |bc -l) )); then
                #echo found a new min: $speed
                avg_speed=$speed
                url=${urls[$count]}
                #echo setting URL to $url
             fi
             count=$((count+1))                                                                                                                                  
          done  # ed of Speed for loop
          echo using server $url
              
          container_pull="curl -X GET ${url}${container} -O"
       else 
          aria_args=""
          if [[ -n "${url_awss3+x}" ]]; then
             aria_args="${aria_args} ${url_awss3}${container}"
          fi
          if [[ -n "${url_nectar+x}" ]]; then
             aria_args="${aria_args} ${url_nectar}${container}"
          fi
          container_pull="aria2c $aria_args"
       fi # end of aria2c check
   else # end of check if files exist in object storage
      # Last resort: docker pull (apptainer converts to SIF on the fly). Prefer Quay, then GHCR.
      echo "$container not in cvmfs / referrers / object storage - falling back to docker pull"
      ts_docker_tag="${containerVersion}_${containerDate}"
      ts_q_token=$(curl -sL "https://quay.io/v2/auth?service=quay.io&scope=repository:neurodesk/${containerName}:pull" 2>/dev/null | jq -r '.token // empty' 2>/dev/null)
      if [ -n "$ts_q_token" ] && curl -sfIL -o /dev/null \
            -H "Authorization: Bearer $ts_q_token" \
            -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.docker.distribution.manifest.list.v2+json" \
            "https://quay.io/v2/neurodesk/${containerName}/manifests/${ts_docker_tag}" 2>/dev/null; then
         echo "  docker pull from Quay"
         storage="quay-docker"
         container_pull="$container_runtime pull --name $container docker://quay.io/neurodesk/${containerName}:${ts_docker_tag}"
      else
         echo "  docker pull from GHCR"
         storage="ghcr-docker"
         container_pull="$container_runtime pull --name $container docker://ghcr.io/neurodesk/${containerName}:${ts_docker_tag}"
      fi
   fi
fi


echo "deploying in $_base"
# echo "checking if container needs to be downloaded"
if  [[ -e $container ]]; then
   echo "container downloaded already. Remove to re-download!"
else
   echo "pulling image now ..."
   echo "where am I: $PWD"
   if ! run_container_pull_with_fallback; then
      fail "Failed to retrieve container '${container}'."
   fi
fi

if [[ ! -e "$container" ]]; then
   fail "Container '${container}' was not created by the retrieval step."
fi

if [[ ${unpack:-} = "true" ]]
then
   echo "unpacking singularity file to sandbox directory:"
   if ! singularity build --sandbox temp $container; then
      fail "Failed to unpack container '${container}'."
   fi
    rm -rf $container
    mv temp $container
fi

rm -f README.md commands.txt commands_raw.txt env.txt

echo "checking if there is a README.md file in the container"
echo "executing: singularity exec $singularity_opts --pwd $_base $container cat /README.md"
if ! singularity exec $singularity_opts --pwd $_base $container cat /README.md > README.md; then
   echo "[WARN] run_transparent_singularity.sh: Could not read /README.md from container '${container}'. Continuing with empty module help." >&2
   : > README.md
fi

echo "checking which executables exist inside container"
echo "executing: singularity exec $singularity_opts --pwd $_base $container $_base/ts_binaryFinder.sh"
if ! singularity exec $singularity_opts --pwd $_base $container $_base/ts_binaryFinder.sh; then
   fail "Could not inspect executables in container '${container}'. Not creating wrapper or module files."
fi

if [[ ! -f "$_base/commands.txt" ]]; then
   fail "ts_binaryFinder.sh did not create commands.txt for '${container}'. Not creating wrapper or module files."
fi

if [[ ! -f "$_base/env.txt" ]]; then
   fail "ts_binaryFinder.sh did not create env.txt for '${container}'. Not creating wrapper or module files."
fi

echo "create singularity executable for each regular executable in commands.txt"
# $@ parses command line options.
#test   executable="fslmaths"

# The --env option requires singularity > 3.6 or apptainer. Test here:
required_version="3.6"
if which apptainer >/dev/null 2>&1; then
    echo "Apptainer is installed."
    singularity_version=3.6
else
    echo "Apptainer is not installed. Testing for singularity version."
    singularity_version=$(singularity version | cut -d'-' -f1)
fi

while read executable; do \
   echo $executable > $_base/${executable}; \
   echo "#!/usr/bin/env bash" > $executable
   echo "export PWD=\`pwd -P\`" >> $executable
   echo 'xauthority_opts=()' >> $executable
   echo 'if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then' >> $executable
   echo '  xauthority_opts=(--bind "$XAUTHORITY:$XAUTHORITY:ro" --env "XAUTHORITY=$XAUTHORITY")' >> $executable
   echo 'fi' >> $executable

   # neurodesk_singularity_opts is a global variable that can be set in neurodesk for example --nv for gpu support
   # --silent is required to suppress bind mound warnings (e.g. for /etc/localtime)
   # --cleanenv is required to prevent environment variables on the host to affect the containers (e.g. Julia and R packages), but to work 
   # correctly with GUIs, the DISPLAY variable needs to be set as well. This only works in singularity >= 3.6.0
   # --bind is needed to handle non-default temp directories (Github issue #11)
   for customtmp in TMP TMPDIR TEMP TEMPDIR; do
      eval tmpvar=\$$customtmp
      if [[ -n $tmpvar ]]; then
         bindtmpdir="--bind \$$customtmp:/tmp"
      fi
   done
   if printf '%s\n' "$required_version" "$singularity_version" | sort -V | head -n1 | grep -q "$required_version"; then
      echo "singularity --silent exec --cleanenv --env DISPLAY=\$DISPLAY \"\${xauthority_opts[@]}\" $bindtmpdir \$neurodesk_singularity_opts --pwd \"\$PWD\" $_base/$container $executable \"\$@\"" >> $executable
   else
      echo "Singularity version is older than $required_version. GUIs will not work correctly!"
      echo "singularity --silent exec --cleanenv $bindtmpdir \$neurodesk_singularity_opts --pwd \"\$PWD\" $_base/$container $executable \"\$@\"" >> $executable
   fi

   chmod a+x $executable
done < $_base/commands.txt

echo "creating activate script that runs deactivate first in case it is already there"
echo "#!/usr/bin/env bash" > activate_${container}.sh
echo "source deactivate_${container}.sh $_base" >> activate_${container}.sh
echo -e "export PWD=\`pwd -P\`" >> activate_${container}.sh
echo -e 'export PATH="$PWD:$PATH"' >> activate_${container}.sh
echo -e 'echo "# Container in $PWD" >> ~/.bashrc' >> activate_${container}.sh
echo -e 'echo "export PATH="$PWD:\$PATH"" >> ~/.bashrc' >> activate_${container}.sh
chmod a+x activate_${container}.sh

echo "deactivate script"
echo  pathToRemove=$_base | cat - ts_deactivate_ > temp && mv temp deactivate_${container}.sh
chmod a+x deactivate_${container}.sh


# e.g. export container=matlab_2024b_20250117
echo "create module files one directory up"
modulePath=$_base/../modules/`echo $container | cut -d _ -f 1`
echo $modulePath
# e.g. ../modules/matlab
mkdir $modulePath -p

moduleSoftwareName=`echo $container | cut -d _ -f 1`
# e.g. matlab

moduleName=`echo $container | cut -d _ -f 2`
# e.g. 2024b

echo "-- -*- lua -*-" > ${modulePath}/${moduleName}.lua
echo "help([===[" >> ${modulePath}/${moduleName}.lua 
bash "$_base/ts_sanitize_lua_help.sh" README.md >> ${modulePath}/${moduleName}.lua
echo "]===])" >> ${modulePath}/${moduleName}.lua

echo "whatis(\"${container}\")" >> ${modulePath}/${moduleName}.lua
echo "prepend_path(\"PATH\", \"${_base}\")" >> ${modulePath}/${moduleName}.lua

echo "create environment variables for module file"
while read envvariable; do \
   # envvariable="DEPLOY_ENV_SPMMCRCMD=BASEPATH/opt/spm12/run_spm12.sh BASEPATH/opt/mcr/v97/ script"
   value=${envvariable#*=}
   # echo $value #BASEPATH/opt/spm12/run_spm12.sh BASEPATH/opt/mcr/v97/ script"

   value_with_basepath="${value//BASEPATH/${_base}/${container}}"
   # echo $value_with_basepath

   completeVariableName=${envvariable%=*}
   # echo $completeVariableName

   variableName=${completeVariableName#*DEPLOY_ENV_}
   # echo $variableName

   echo "setenv(\"${variableName}\", \"${value_with_basepath}\")" >> ${modulePath}/${moduleName}.lua
done < $_base/env.txt

#check if there is a manual module file for this container and add it to the end
if [[ -e manual_module_files/${moduleSoftwareName} ]]; then
   echo "addming manual module file"
   cat manual_module_files/${moduleSoftwareName} | sed "s/toolVersion/${moduleName}/g" >> ${modulePath}/${moduleName}.lua
fi

echo "rm ${modulePath}/${moduleName}" >> ts_uninstall.sh
