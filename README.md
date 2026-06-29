# VME Helper Tools

Shared utilities and tool scripts for automating Team Center workflows (VME downloads, conversions, etc.).

## Project Structure

```
vme-helper-tools/
├── scripts/                                  # Shared Python package (importable)
│   ├── auth.py                               # Azure AD / MSAL authentication
│   ├── teamcenter.py                         # Team Center session & paginated search + API wrappers
│   ├── tc_output.py                          # Output formatting (JSON, CSV, file/stdout)
│   ├── vme.py                                # VME enumeration, revision, download
│   ├── maya_convert.py                       # Maya standalone init/teardown, .mb→.ma
│   ├── logging.py                            # Shared logging configuration
│   └── paths.py                              # Corporate dependency path resolution
├── tools/                                    # Tool scripts (all require mayapy unless noted)
│   ├── tc_api.py                             # TC REST API CLI — all endpoints (no Maya needed)
│   ├── fetch_and_convert_ma.py               # Download VMEs from TC and convert .mb → .ma
│   ├── export_vme_list.py                    # Export CSV of VMEs (no Maya needed)
│   ├── pipeline_full_corpus.py               # Full pipeline: fetch + extract + collect (resumable)
│   └── transformation/                       # Render Manifestation extraction variants
│       ├── extract_render_manifestation.py       # Option A: Normal hierarchy
│       ├── extract_render_manifestation_flat.py  # Option B: Flat (no Styles node)
│       ├── extract_render_manifestation_module.py # Option C: Module (no Styles or Render)
│       ├── extract_render_manifestation_style.py  # Option D: Style (CommonParts separated)
│       └── diagnose_render_manifestation.py      # Diagnostic + export-style replication
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

Uses **Azure AD (Entra ID)** via MSAL `PublicClientApplication` with OAuth2 interactive browser flow.

### Credentials

| Setting | Value |
|---------|-------|
| Client ID | `cf749082-77bc-4abd-aea9-92b0d337f38a` |
| Tenant ID | `1d063515-6cad-4195-9486-ea65df456faa` |
| Authority | `https://login.microsoftonline.com/1d063515-6cad-4195-9486-ea65df456faa` |
| Scopes | `api://cf749082-77bc-4abd-aea9-92b0d337f38a/user_impersonation` |
| Token type | Bearer (injected in `Authorization` header) |

### How it works

1. On first run, a browser window opens for LEGO Azure AD login (your personal corporate account)
2. After successful login, the access token is cached by MSAL **in memory only** (no persistent disk cache)
3. Subsequent API calls within the same process reuse the cached token silently
4. Token auto-refreshes if it expires during a long batch run (~1 hour validity)
5. Every new process invocation requires a fresh interactive login

There is **no service account** — all API calls are made as the authenticated user (`user_impersonation` scope). The username is logged on connection: `Connected to Team Center (prod) as: {username}`.

### API Request Pattern

All requests go through `team_center.TeamCenter.Session` (from `dep-dwf-maya-python`):

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Get item by ID | GET | `/items/{TCID}?allrevs` |
| Search items | POST | `/items?ObjType=VME&_pagesize=50` |
| Next search page | POST | (uses cursor from previous response) |
| Download spec file | GET | `/items/{TCID}/specifications/mb/vme.mb?Rev={REV}` |
| Get session info | GET | `/session` |
| Search by scheme | POST | `/search/scheme/{scheme}` |
| BOM product | GET | `/boms/product/{id}` |
| Resolve identifier | GET | `/resolver/{id}` |
| Report (3DFLOW) | GET | `{base}/lego/Report/3DFLOW.list.type?argA={type}` |

Response data lives under the `_DATA` key. Pagination is handled by `paginate_search()` which calls `search_items` then loops `search_items_next_page` until exhausted.

## TLS / CA Certificates

Maya's bundled Python (`mayapy.exe`) does not ship a system CA certificate store. The tooling automatically sets `REQUESTS_CA_BUNDLE` to the LEGO CA bundle at `dep-dwf-maya-python/python_externals/team_center_qnetwork/lego_ca/lego_cacert.crt`. This is the same mechanism used by the ved_tool (set at import time via `team_center_qnetwork.Network`).

Without this bundle, HTTPS connections to `*.corp.lego.com` will **hang indefinitely** (the corporate CA is not in the default system trust store).

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

### TC API CLI

Unified command-line access to all Team Center REST API endpoints. No Maya required. Covers items, BOMs, recipes, materials, search schemes, workflows, and more.

```bash
poetry run python tools/tc_api.py <command> [options]
```

#### Subcommands

| Command | Description |
|---------|-------------|
| `auth` | Verify authentication |
| `session` | Show session info |
| `items get TCID` | Get item by TCID (`--allrevs`, `--allfiles`) |
| `items search` | Search items (`--type`, `--field K=V`, `--sort`, `--references`) |
| `items refs TCID` | Find items referencing a TCID |
| `items checkout TCID [...]` | Checkout items |
| `items checkin TCID [...]` | Checkin items |
| `items revise TCID [...]` | Revise items |
| `items set-refs TCID` | Set reference relationships (`--ref TCID:REV:NAME`) |
| `items props TCID` | Update properties (`--json-file PATH`) |
| `items create TYPE NAME` | Create item (`--json-file PATH`) |
| `items workflow NAME TCID [...]` | Trigger workflow |
| `items baseline NAME TCID [...]` | Set baseline |
| `items release NAME TCID [...]` | Release items |
| `bom get PRODUCT_ID` | Get BOM (`--explode`) |
| `bom search SCHEME` | Search using scheme (`--field K=V`) |
| `recipes get ID` | Get recipe |
| `recipes update ID` | Update recipe (`--json-file PATH`) |
| `recipes delete ID` | Delete recipe |
| `materials TCID` | Get material by TCID |
| `masterdata` | Get master data |
| `search scheme NAME` | Get search scheme definition |
| `search run NAME` | Execute scheme-based search (`--field K=V`) |
| `resolver ID` | Resolve identifier |
| `magic TCID REV` | Get magic hash |
| `report` | List elements by type (`--type TYPE`) |

#### Global Options

| Option | Default | Description |
|--------|---------|-------------|
| `--env` | `prod` | Team Center environment (`dev` or `prod`) |
| `--format` | `json` | Output format (`json` or `csv`) |
| `--output PATH` | stdout | Write result to file |
| `--page-size` | `50` | Search pagination size |
| `--timeout` | `120` | Request timeout in seconds |
| `--dry-run` | — | Skip write operations |
| `--log-level` | `INFO` | Logging verbosity |
| `--log-file` | — | Write logs to file |

#### Examples

```bash
# Verify authentication against dev
poetry run python tools/tc_api.py auth --env dev

# Get item with all revisions
poetry run python tools/tc_api.py items get VX0003001 --allrevs

# Search for VMEs sorted by modification date
poetry run python tools/tc_api.py items search --type VME --sort -ModifiedDate --page-size 10

# Search with custom fields
poetry run python tools/tc_api.py items search --type VME --field SuperDesign=11206229 --field IsVariant=true

# Export search results as CSV
poetry run python tools/tc_api.py items search --type VME --format csv --output vmes.csv

# Find items referencing a specific VME
poetry run python tools/tc_api.py items refs VX0003626

# Get a BOM with exploded tree
poetry run python tools/tc_api.py bom get 50075309 --explode

# Get recipe data
poetry run python tools/tc_api.py recipes get 6448586

# Resolve a design ID
poetry run python tools/tc_api.py resolver 11206886

# Get magic hash for upload
poetry run python tools/tc_api.py magic VX0003001 A

# List all decoration elements
poetry run python tools/tc_api.py report --type decoration

# Checkout items (dry-run to preview)
poetry run python tools/tc_api.py items checkout VX0003001 --dry-run --env dev

# Set references on an item
poetry run python tools/tc_api.py items set-refs VX1000556 --ref VX0002544:B:VariantOf

# Trigger render workflow
poetry run python tools/tc_api.py items workflow LE7_RENDER_WORKFLOW VX0003001 --env dev

# Search using Y950 scheme
poetry run python tools/tc_api.py bom search Y950 --field materialNo=50075309
```

### Running Without Maya (Bundled Environment)

If you cannot use `mayapy`, install dependencies directly:

```bash
pip install msal requests
```

Then adjust the `sys.path` setup in the tool script accordingly. Note: Maya conversion features will not be available.
