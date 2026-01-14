# Neurocommand Apps.json System Documentation

## Overview

The `apps.json` system is the central registry for all neuroimaging containers available through Neurocommand/Neurodesk. This system manages container metadata, automates container building and publishing, and generates user-facing interfaces for accessing containerized neuroimaging tools.

## System Components

### 1. Core Configuration File (`neurodesk/apps.json`)

The `apps.json` file at `/neurodesk/apps.json` serves as the master registry containing:
- Container names and versions
- Version dates (build dates) 
- Execution commands for GUI variants
- Category classifications for organization

### 2. Key Processing Scripts

#### `neurodesk/write_log.py`
- **Purpose**: Generates container inventory from `apps.json`
- **Function**: Parses `apps.json` and creates `log.txt` with container list
- **Output Format**: `{container_name}_{version}_{date} categories:{category1,category2}`
- **Location**: `/neurodesk/write_log.py:39`

#### `neurodesk/build_menu.py`
- **Purpose**: Creates desktop menu entries and launcher scripts
- **Functions**:
  - Parses `apps.json` via `apps_from_json()` function
  - Generates `.desktop` files for GUI integration
  - Creates executable scripts in `/bin` directory
  - Manages application icons and categories
- **Location**: `/neurodesk/build_menu.py:195-218`

#### `cvmfs/json_gen.py`  
- **Purpose**: Converts log.txt to website-friendly JSON format
- **Output**: Generates `cvmfs/applist.json` for website display
- **Location**: `/cvmfs/json_gen.py`

### 3. Container Management Scripts

#### `neurodesk/fetch_containers.sh`
- **Purpose**: Downloads and manages Singularity containers
- **Process**:
  - Checks if container exists locally
  - Downloads from cloud storage if needed
  - Validates container integrity
  - Sets up transparent-singularity integration
- **Location**: `/neurodesk/fetch_containers.sh`

#### `neurodesk/fetch_and_run.sh`
- **Purpose**: Launcher script for executing containers
- **Process**: Called by generated menu entries to run specific tools

## Apps.json Schema

### Top-Level Structure
```json
{
  "container_name": {
    "apps": {
      "app_name version": {
        "version": "YYYYMMDD",
        "exec": "command_to_execute"
      }
    },
    "categories": ["category1", "category2"]
  }
}
```

### Field Definitions

#### Container Level
- **Key**: Container name (e.g., "afni", "fsl", "freesurfer")
- **apps**: Object containing all versions/variants of the container
- **categories**: Array of category strings for organization

#### Application Level  
- **Key**: Application name with version (e.g., "afni 24.3.00", "fsleyesGUI-fsl 6.0.7.16")
- **version**: Build date in YYYYMMDD format (e.g., "20241003")
- **exec**: Command to execute when launching (empty string "" for CLI-only)

### Naming Conventions

#### GUI Applications
GUI variants use the pattern: `{executable}GUI-{container} {version}`
- Example: `"fsleyesGUI-fsl 6.0.7.16"` with `"exec": "fsleyes"`
- Example: `"3DSlicerGUI-slicer 5.0.3"` with `"exec": "Slicer"`

#### CLI Applications  
CLI applications use: `{container} {version}`
- Example: `"fsl 6.0.7.16"` with `"exec": ""`
- Empty exec string indicates command-line only access

#### Multiple Executables
Containers can have multiple GUI variants:
```json
"mgltools": {
  "apps": {
    "pmvGUI-mgltools 1.5.7": {"version": "20230313", "exec": "pmv"},
    "visionGUI-mgltools 1.5.7": {"version": "20230313", "exec": "vision"},
    "adtGUI-mgltools 1.5.7": {"version": "20230313", "exec": "adt"}
  }
}
```

### Category System

#### Available Categories
- **functional imaging**: fMRI analysis tools
- **structural imaging**: Anatomical image processing  
- **diffusion imaging**: DTI/DWI analysis
- **image segmentation**: Segmentation tools
- **image registration**: Registration/alignment tools
- **electrophysiology**: EEG/MEG analysis
- **workflows**: Pipeline tools (fmriprep, mriqc)
- **data organisation**: BIDS tools, converters
- **visualization**: Viewing and display tools
- **programming**: Development environments
- **quantitative imaging**: Quantitative analysis
- **phase processing**: Phase/QSM processing
- **spectroscopy**: MRS analysis
- **machine learning**: ML/AI tools
- **quality control**: QC tools
- **bids apps**: BIDS-compatible applications
- **cryo EM**: Cryo-electron microscopy
- **molecular biology**: Structural biology tools
- **rodent imaging**: Small animal imaging
- **spine**: Spinal cord analysis
- **hippocampus**: Hippocampal analysis
- **body**: Body imaging tools
- **shape analysis**: Shape analysis tools
- **statistics**: Statistical analysis
- **image reconstruction**: Reconstruction tools

## Container Publishing Workflow

The container publishing workflow involves two repositories working together:

### A. Neurocontainers Repository: Container Creation and Building

#### 1. Recipe-Based Container Development
**Location**: `neurocontainers` repository (`recipes/` directory)

**Trigger**: Push to `main` branch that modifies:
- `recipes/**` - Any changes to recipe files

**Workflow Files**: 
- `.github/workflows/auto-build.yml` - Detects recipe changes
- `.github/workflows/build-apps.yml` - Core container building
- `.github/workflows/manual-build.yml` - Manual triggers

#### 2. Automated Build Process

**Step 1: Change Detection** (`.github/workflows/auto-build.yml:22-49`)
```bash
# Detect changed recipe directories
changed_recipes=$(echo "${{ steps.find_changed_dirs.outputs.all_changed_files }}" | jq -rc '.[]' | cut -d/ -s -f 2-2)

# Filter for auto-build enabled applications
AUTOBUILD=$(cat .github/workflows/build-config.json | jq ".${APPLICATION} .autoBuild")
```

**Step 2: Container Generation** (`.github/workflows/build-apps.yml:74-77`)
```bash
# Generate Dockerfile from YAML recipe
./builder/build.py generate $APPLICATION --recreate --auto-build
```

**Step 3: Multi-Format Building** (`.github/workflows/build-apps.yml:98-99`)
```bash
# Build Docker and Singularity images
/bin/bash .github/workflows/build-docker-and-simg.sh $IMAGENAME
```

**Step 4: Cloud Storage Upload** (`.github/workflows/build-apps.yml:100-108`)
```bash
# Upload to Nectar cloud storage
/bin/bash .github/workflows/upload-nectar.sh $IMAGENAME

# Upload to AWS S3
/bin/bash .github/workflows/upload-aws-s3.sh $IMAGENAME
```

#### 3. Issue-Driven Integration Process

**Automatic Issue Creation** (`.github/workflows/build-apps.yml:114-119`)
When a container builds successfully, the workflow automatically creates an issue using:
- Template: `.github/new_container_issue_template.md`
- Contains testing instructions and apps.json update guidance
- Uses `NEURODESK_GITHUB_TOKEN_ISSUE_AUTOMATION` for cross-repo access

**Issue Template Content**:
```markdown
There is a new container by @{{ env.GITHUB_ACTOR }}, use this command to test on Neurodesk:
bash /neurocommand/local/fetch_and_run.sh {{ env.IMAGENAME_TEST }} {{ env.BUILDDATE }}

If test was successful, then add to apps.json to release to Neurodesk:
https://github.com/NeuroDesk/neurocommand/edit/main/neurodesk/apps.json
```

#### 4. Container Naming and Versioning

**Image Name Generation** (`.github/workflows/build-apps.yml:80-91`)
```bash
IMAGENAME=$(echo $(basename $DOCKERFILE .Dockerfile) | tr '[A-Z]' '[a-z]')
BUILDDATE=`date +%Y%m%d`
IMAGENAME_TEST=${IMAGENAME//_/ }  # Converts underscores to spaces for testing
```

**Storage Format**: `{imagename}_{builddate}.simg`
- Example: `afni_24.3.00_20241003.simg`

#### 5. Testing Instructions

**Neurodesk Testing**:
```bash
bash /neurocommand/local/fetch_and_run.sh {imagename_test} {builddate}
```

**Direct Singularity Testing**:
```bash
curl -X GET https://neurocontainers.s3.us-east-2.amazonaws.com/temporary-builds-new/{imagename}_{builddate}.simg -O
singularity shell --overlay /tmp/apptainer_overlay {imagename}_{builddate}.simg
```

### B. Neurocommand Repository: Apps.json Integration and Distribution

#### 1. Manual Apps.json Update Process
**Current State**: Semi-automated workflow requiring human intervention

1. **Issue Review**: Maintainers review auto-generated issues from neurocontainers repo
2. **Container Testing**: Test containers using provided commands
3. **Manual apps.json Edit**: If testing successful, manually add entries to `neurodesk/apps.json`
4. **Automatic Processing**: Changes trigger neurocommand workflows

#### 2. Automated Container Detection
**Trigger**: Push to `main` branch that modifies:
- `.github/workflows/update-neurocontainers.yml`
- `neurodesk/apps.json` 
- `neurodesk/write_log.py`

**Workflow File**: `.github/workflows/update-neurocontainers.yml`

### 2. Container Processing Pipeline

#### Step 1: Generate Container List
```bash
python3 neurodesk/write_log.py
```
- Reads `apps.json`
- Generates `log.txt` with container specifications
- Format: `{name}_{version}_{date} categories:{cats}`

#### Step 2: Container Availability Check
For each container in `log.txt`:
- Check if `.simg` file exists in Nectar cloud storage
- Check temporary build cache if not in main storage
- If neither exists, trigger new build

#### Step 3: Container Building (if needed)
```bash
singularity build {container}.simg docker://vnmd/{name}:{date}
```
- Uses Apptainer/Singularity to build from Docker images
- Docker images hosted at `docker://vnmd/`
- Build date corresponds to specific image tags

#### Step 4: Cloud Storage Upload
- Upload to Nectar Cloud: `nectar:/neurodesk/`
- Sync to AWS S3: `aws-neurocontainers-new:/neurocontainers/`
- Cleanup old containers (>30 days)

#### Step 5: Generate Website Assets
```bash
cd cvmfs
python json_gen.py
```
- Converts `log.txt` to `applist.json`
- Used by Neurodesk website for container listing
- Auto-commits changes via GitHub Actions

### 3. Storage Locations

#### Primary Storage (Nectar Cloud)
- URL: `https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/neurodesk/`
- Format: `{container}_{version}_{date}.simg`
- Example: `afni_24.3.00_20241003.simg`

#### Backup Storage (AWS S3)
- Bucket: `s3://neurocontainers/`
- Synced from Nectar cloud
- Provides redundancy and global access

#### Temporary Build Cache
- Location: `nectar:/neurodesk/temporary-builds-new/`
- Stores containers during build process
- Cleaned up after successful transfer (7-30 days)

## Integration Points

### 1. Desktop Environment Integration
The `build_menu.py` script creates:
- **Desktop Files**: `.desktop` entries for application launchers
- **Menu Categories**: Organized by neuroimaging domain
- **Shell Scripts**: Executable wrappers in `/bin` directory
- **Icons**: Application icons in `/icons` directory

### 2. Container Runtime Integration
- Uses transparent-singularity for seamless execution
- Automatic path mounting and environment setup
- Module system integration for HPC environments

### 3. Website Integration
- `cvmfs/applist.json` consumed by Neurodesk website
- Displays available containers and categories
- Updates automatically on apps.json changes

### 4. CVMFS Distribution
- Containers distributed via CVMFS for global access
- `cvmfs/sync_containers_to_cvmfs.sh` manages synchronization
- Provides low-latency access worldwide

## Adding New Containers

There are two main approaches to adding new containers to the Neurodesk ecosystem:

### A. Contributing via Neurocontainers Repository (Recommended)

This is the preferred method for adding new neuroimaging tools as it follows the automated build and testing workflow.

#### 1. Create Recipe in Neurocontainers Repository

**Recipe Structure**: Create a YAML recipe file in `recipes/{toolname}/` 

**Example Recipe Format**:
```yaml
name: mytool
version: 1.0.0
dependencies:
  - ubuntu:20.04
install:
  - apt update && apt install -y build-essential
  - # Tool-specific installation commands
  
deploy:
  bins:
    - mytool
    - mytool-gui
  path:
    - /opt/mytool/
    
# Optional build configuration
autoBuild: true  # Enable automatic building
freeUpSpace: false  # Manage disk space for large builds
```

#### 2. Configure Auto-Build Settings

**File**: `.github/workflows/build-config.json`

Add or verify entry for your application:
```json
{
  "mytool": {
    "autoBuild": true,
    "freeUpSpace": false
  }
}
```

**Auto-Build Options**:
- `true`: Container builds automatically when recipe changes
- `false`: Requires manual triggering via `manual-build.yml`

#### 3. Automatic Build Process

When you push changes to `recipes/mytool/`:

1. **Auto-Detection**: `auto-build.yml` detects recipe changes
2. **Build Triggering**: Only applications with `autoBuild: true` are processed
3. **Container Generation**: `./builder/build.py` creates Dockerfile from YAML recipe
4. **Multi-Format Building**: Creates both Docker (`vnmd/mytool:YYYYMMDD`) and Singularity images
5. **Cloud Upload**: Uploads to Nectar and AWS S3 storage
6. **Issue Creation**: Automatically creates testing issue in neurocommand repository

#### 4. Testing Phase

The automated issue will contain:

**Testing Commands**:
```bash
# Neurodesk testing
bash /neurocommand/local/fetch_and_run.sh mytool 1.0.0 20240617

# Direct Singularity testing  
curl -X GET https://neurocontainers.s3.us-east-2.amazonaws.com/temporary-builds-new/mytool_1.0.0_20240617.simg -O
singularity shell --overlay /tmp/apptainer_overlay mytool_1.0.0_20240617.simg
```

#### 5. Apps.json Integration

After successful testing, maintainers add the container to `neurodesk/apps.json`:

```json
"mytool": {
  "apps": {
    "mytool 1.0.0": {
      "version": "20240617",
      "exec": ""
    },
    "mytool-guiGUI-mytool 1.0.0": {
      "version": "20240617",
      "exec": "mytool-gui"
    }
  },
  "categories": ["appropriate", "categories"]
}
```

#### 6. Workflow Benefits

- **Automated Testing**: Built containers are automatically tested
- **Quality Assurance**: Human review before apps.json integration
- **Version Control**: Recipe-based approach ensures reproducibility
- **Multi-Platform**: Supports both Docker and Singularity formats
- **Issue Tracking**: Clear communication between repos via GitHub issues

### B. Direct Apps.json Update (Manual Method)

Use this method only when containers already exist or for urgent updates.

#### 1. Container Requirements
- Docker image must exist at `docker://vnmd/{name}:{date}`
- Image should be properly tagged with build date
- Container must be functional with Singularity/Apptainer

#### 2. Update apps.json
Add entry to `/neurodesk/apps.json`:
```json
"newcontainer": {
  "apps": {
    "newcontainer 1.0.0": {
      "version": "20240101",
      "exec": ""
    },
    "newcontainerGUI-newcontainer 1.0.0": {
      "version": "20240101", 
      "exec": "newcontainer-gui"
    }
  },
  "categories": ["appropriate", "categories"]
}
```

#### 3. Validation Steps
- Ensure Docker image builds successfully
- Test container execution with Singularity
- Verify GUI applications launch properly
- Confirm appropriate categories are assigned

#### 4. Automatic Processing
- Push changes to trigger workflow
- Monitor GitHub Actions for build success
- Verify container appears in generated outputs

### Recommended Workflow Summary

1. **For New Tools**: Use neurocontainers repository recipe approach
2. **For Updates**: Update existing recipes in neurocontainers repository  
3. **For Urgent Fixes**: Direct apps.json updates in neurocommand repository
4. **For Testing**: Always test containers before apps.json integration

## Troubleshooting

### Common Issues

#### Container Build Failures
- **Cause**: Docker image doesn't exist or is corrupted
- **Solution**: Verify Docker image at `docker://vnmd/{name}:{date}`
- **Check**: Review build logs in GitHub Actions

#### Missing Categories
- **Cause**: Invalid category names in apps.json
- **Solution**: Use only predefined categories from the list above
- **Check**: Review `build_menu.py:271-295` for valid categories

#### GUI Applications Not Launching
- **Cause**: Incorrect `exec` command or missing dependencies
- **Solution**: Test exec command inside container manually
- **Check**: Verify X11 forwarding and display setup

#### Storage Upload Failures  
- **Cause**: Network issues or permission problems
- **Solution**: Check cloud storage credentials and network
- **Check**: Monitor rclone sync operations in logs

### Debug Tools

#### Manual Container Testing
```bash
# Test container download
bash neurodesk/fetch_containers.sh {name} {version} {date}

# Test container execution  
singularity exec {container}.simg {command}

# Test GUI application
singularity exec {container}.simg {gui_command}
```

#### Log Analysis
- **Build Logs**: GitHub Actions workflow logs
- **Container Logs**: `log.txt` generated by `write_log.py`
- **Upload Logs**: rclone operation logs in workflow

## Security Considerations

### Container Validation
- All containers built from trusted Docker images
- Singularity provides isolation and security
- No privileged access required for execution

### Storage Security
- Cloud storage uses authenticated access
- GitHub secrets manage cloud credentials
- No sensitive data stored in containers

### Access Control
- Containers run with user permissions
- No sudo access required for normal operation
- Transparent-singularity handles path mapping safely

## Performance Considerations

### Build Optimization
- Parallel container processing where possible
- Caching reduces redundant builds
- Cleanup prevents storage bloat

### Distribution Optimization  
- CVMFS provides global caching
- Multiple storage backends ensure availability
- Compression reduces transfer times

### Runtime Optimization
- Containers cached locally after first use
- Module system enables rapid loading
- Transparent execution minimizes overhead

## Maintenance Tasks

### Regular Maintenance
- Monitor storage usage and cleanup old containers
- Update container versions as software releases
- Review and update category classifications
- Test GUI applications on different desktop environments

### Emergency Procedures
- Rollback apps.json changes if builds fail
- Manual container upload for critical tools
- Disable problematic containers temporarily
- Contact cloud storage providers for access issues

## Future Enhancements

### Planned Improvements
- Automated container testing before publication
- Version dependency management
- Enhanced metadata for containers
- Integration with package managers
- Automated security scanning

### Integration Opportunities
- Container registry integration
- Enhanced GUI application support
- Cloud-native execution options
- Kubernetes deployment support