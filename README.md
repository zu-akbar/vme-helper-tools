# VME Helper Tools

Shared utilities and tool scripts for automating Team Center workflows (VME downloads, conversions, etc.).

## Project Structure

```
vme-helper-tools/
├── scripts/                                  # Shared Python package (importable)
│   ├── auth.py                               # Azure AD / MSAL authentication
│   ├── teamcenter.py                         # Team Center session & paginated search
│   ├── vme.py                                # VME enumeration, revision, download
│   ├── maya_convert.py                       # Maya standalone init/teardown, .mb→.ma
│   ├── logging.py                            # Shared logging configuration
│   └── paths.py                              # Corporate dependency path resolution
├── tools/                                    # Tool scripts (all require mayapy unless noted)
│   ├── fetch_and_convert_ma.py               # Download VMEs from TC and convert .mb → .ma
│   ├── export_vme_list.py                    # Export CSV of VMEs (no Maya needed)
│   ├── extract_render_manifestation.py       # Extract Render-only .ma (removes other styles)
│   ├── extract_render_manifestation_flat.py  # Same but flattens Styles node out
│   └── diagnose_render_manifestation.py      # Diagnostic + export-style (ved_tool replication)
└── data/
    ├── input/                                # Source files (xls, pdf, csv)
    └── output/                               # Generated outputs (gitignored)
```

## Setup

Requires Python >= 3.9 and [Poetry](https://python-poetry.org/).

```bash
poetry install
```

## System Requirements

| Requirement | Details |
|-------------|---------|
| OS | Windows 10/11 |
| Maya | Autodesk Maya 2023 (for VME ASCII generator) |
| Network | Access to `*.corp.lego.com` (LEGO corporate network or VPN) |
| Authentication | LEGO Azure AD account (OAuth2 interactive login via browser) |

## Sibling Repository Dependency

The tools expect `dep-dwf-maya-python` checked out as a sibling directory:

```
Dev/Git/
├── dep-dwf-maya-python/         # Provides python_externals/ and Scripts/
│   ├── python_externals/
│   └── Scripts/
└── vme-helper-tools/
```

This is resolved automatically by `scripts.paths.ensure_dep_paths_on_sys_path()`.

## Python Dependencies

Managed via Poetry (`pyproject.toml`). Key packages:

| Package | Purpose |
|---------|---------|
| `msal` | Microsoft Authentication Library — OAuth2 token acquisition |
| `requests` | HTTP client for Team Center REST API |

Transitive: `urllib3`, `certifi`, `cryptography`, `PyJWT`.

Additional packages from `dep-dwf-maya-python/python_externals/` (loaded at runtime):

| Package | Purpose |
|---------|---------|
| `team_center` | Team Center REST API client library |
| `lego_logger` | LEGO internal logging framework |
| `six` | Python 2/3 compatibility |

## Team Center API Endpoints

| Environment | URL |
|-------------|-----|
| `prod` | `https://dkatcpp-a1.corp.lego.com/legotcapi2` |
| `dev` | `https://dkatcpr-a1.corp.lego.com/legotcapi2` |

These are the OAuth2/JWT-accepting endpoints (port 443). The legacy hosts on port 8443 (`dkaapp-res`, `dkadev-api`) use Negotiate/NTLM auth and are not used by this tooling.

The `TC_URL` environment variable can override the endpoint if set (the `team_center` library checks it in `build_url()`).

## Authentication Flow

1. On first run, a browser window opens for LEGO Azure AD login
2. After successful login, tokens are cached by MSAL in memory
3. Subsequent runs within the same session reuse the cached token silently
4. Token auto-refreshes if it expires during a long batch run

## TLS / CA Certificates

Maya's bundled Python (`mayapy.exe`) does not ship a system CA certificate store. The tooling automatically sets `REQUESTS_CA_BUNDLE` to the LEGO CA bundle at `dep-dwf-maya-python/python_externals/team_center_qnetwork/lego_ca/lego_cacert.crt`. This is the same mechanism used by the ved_tool (set at import time via `team_center_qnetwork.Network`).

## Tools

### VME ASCII Generator

Batch downloads VME `.mb` files from Team Center and converts them to `.ma` (Maya ASCII).

Run with Maya's headless Python:

```bash
"C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py [OPTIONS]
```

Examples:

```bash
# Process all VMEs from production Team Center
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --env prod --output-dir C:\output\vme_ascii

# Process specific VME IDs only
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --vme-ids VX0049097 VX0003001

# Process by SuperDesign number
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --super-designs 10000001 10000002

# Dry run — list what would be processed without downloading
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --dry-run

# Keep the .mb files alongside the .ma output
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --keep-mb --output-dir ./output

# Verbose logging to file
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --log-level DEBUG --log-file batch.log
```

### VME List Export

Exports a CSV of all available VMEs with their revisions, type, and release status. No Maya required.

```bash
poetry run python tools/export_vme_list.py [OPTIONS]
```

Examples:

```bash
# Export all VMEs from production
poetry run python tools/export_vme_list.py --env prod --output vme_list.csv

# Filter by SuperDesign
poetry run python tools/export_vme_list.py --super-designs 10000001 10000002

# Verbose logging
poetry run python tools/export_vme_list.py --log-level DEBUG
```

Output CSV columns: `ItemId`, `Name`, `Type`, `SuperDesign`, `Revision`, `ReleaseStatus` (one row per revision).

### Render Manifestation Extraction

Extracts only the Render style from full VME `.ma` files, removing Realtime and BIPrint styles and their objectSets.

```bash
# Standard extraction (keeps Styles|Render hierarchy)
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/extract_render_manifestation.py

# Flat extraction (removes Styles node, Render lives directly under VME root)
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/extract_render_manifestation_flat.py

# Filter to specific VMEs
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/extract_render_manifestation_flat.py --vme-ids VX0003001 VX0049097
```

Output hierarchy (flat variant):
```
VME_11003001
├── Render
│   ├── Shell
│   ├── Detail
│   ├── Caps
│   └── CommonParts
├── Overrides
├── Connectivity
└── Map_Data
```

### Diagnostic & Export-Style

Diagnoses Render Manifestation files and replicates the ved_tool export-style procedure: combines Shell+Detail into a single merged mesh, renames to `m{design_id}`, reparents CommonParts as flat children, generates `Exp_ColorChange` UV sets, and exports as standalone `.ma`.

```bash
# Full diagnostic + export-style
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/diagnose_render_manifestation.py

# Diagnostic only (no export)
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/diagnose_render_manifestation.py --skip-export

# Single VME
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/diagnose_render_manifestation.py --vme-ids VX0003001
```

Export-style output uses ved_tool naming convention (`m11003001.ma`) and is saved to `data/output/Export-Style/`.

### Running Without Maya (Bundled Environment)

If you cannot use `mayapy`, install dependencies directly:

```bash
pip install msal requests
```

Then adjust the `sys.path` setup in the tool script accordingly. Note: Maya conversion features will not be available.
