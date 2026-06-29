# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Tooling for automating Team Center (LEGO PLM) workflows around VMEs — enumerating them, exporting their revision metadata, batch-converting their Maya binary files to ASCII, and extracting Render Manifestations. Two layers: an importable `scripts/` package holding shared infrastructure (auth, TC session, paths, logging, Maya init), and `tools/` holding the runnable CLI entry points.

## Commands

```bash
poetry install                              # set up the in-project .venv

# Export VME revision metadata to CSV (no Maya needed)
poetry run python tools/export_vme_list.py --env prod --output vme_list.csv

# Batch download .mb and convert to .ma — MUST run under Maya's interpreter
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --env prod --output-dir ./output
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --dry-run   # plan only, no auth-to-Maya cost

# Extract Render Manifestation (4 hierarchy variants) — MUST run under mayapy
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/transformation/extract_render_manifestation.py        # Option A: Normal (preserves Styles/Render)
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/transformation/extract_render_manifestation_flat.py   # Option B: Flat (Render under root, no Styles)
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/transformation/extract_render_manifestation_module.py # Option C: Module (no Styles or Render groups)
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/transformation/extract_render_manifestation_style.py  # Option D: Style (CommonParts separated from Render)

# Full corpus pipeline — Fetch + Extract + Collect across all VMEs from CSV — MUST run under mayapy
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/pipeline_full_corpus.py --dry-run                     # preview what will be processed
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/pipeline_full_corpus.py --phase fetch                  # only download + convert
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/pipeline_full_corpus.py --phase extract --phase collect # only extract + collect
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/pipeline_full_corpus.py                                # all phases (resumable)

# TC REST API CLI — unified access to all TC endpoints (no Maya needed)
poetry run python tools/tc_api.py auth --env dev                                    # verify auth
poetry run python tools/tc_api.py items get VX0003001 --allrevs                     # get item
poetry run python tools/tc_api.py items search --type VME --sort -ModifiedDate      # search
poetry run python tools/tc_api.py items search --type VME --format csv              # CSV output
poetry run python tools/tc_api.py bom get 50075309 --explode                        # BOM
poetry run python tools/tc_api.py recipes get 6448586                               # recipe
poetry run python tools/tc_api.py resolver 11206886                                 # resolve ID
poetry run python tools/tc_api.py report --type decoration                          # report
```

There is no test suite, linter config, or build step — this is a script collection, not a packaged library despite the `[build-system]` entry.

## Why two interpreters

`fetch_and_convert_ma.py` imports `maya.standalone` / `maya.cmds`, which only exist inside Maya's bundled Python (`mayapy.exe`). It cannot run under the Poetry venv. Everything else (`export_vme_list.py`, the whole `scripts/` package) is pure Python and runs under Poetry. When editing the converter, keep all `maya.*` imports lazy (inside functions, as they currently are) so the module stays importable for inspection outside Maya.

## Runtime dependency resolution (critical)

The Team Center client library (`team_center`) and `lego_logger` are **not** pip dependencies. They live in a **sibling repo** `dep-dwf-maya-python/` that must be checked out next to this one:

```
Dev/Git/
├── dep-dwf-maya-python/     # provides python_externals/ and Scripts/
└── vme-helper-tools/
```

Each tool bootstraps this at the top of `main`'s module load: insert `REPO_ROOT` on `sys.path`, then call `scripts.paths.ensure_dep_paths_on_sys_path()`, and only **after that** import `team_center.*`. This import ordering is load-bearing — `from team_center... import` lines sit below the `ensure_dep_paths_on_sys_path` call on purpose; don't hoist them to the top.

## Architecture flow

`auth.py` → `teamcenter.py` → tool scripts.

- **`auth.BearerAuth`** is a `requests`-style callable that lazily acquires (and caches/refreshes) an Azure AD token via MSAL — silent if an account exists, interactive browser login otherwise. Corporate client/tenant IDs are hardcoded constants here.
- **`teamcenter.create_tc_session(env)`** wires `BearerAuth` into `team_center.TeamCenter.Session`. `paginate_search` walks `search_items` → `search_items_next_page` until exhausted; results live under the `_DATA` key.
- **Revision model**: items carry `_ALLREVS` (list of revisions, each with `Rev` + `ReleaseStatus`). `"Working"` status = unreleased. `fetch_and_convert_ma.get_final_revision` picks the highest released rev using `team_center.TeamCenterFormats.revision_sort_key`; VMEs with no released revision are skipped. The export tool emits one CSV row per revision instead.

## Team Center API connection details

### Authentication

Uses **Azure AD (Entra ID)** via MSAL `PublicClientApplication` with OAuth2 device/interactive browser flow:

| Setting | Value |
|---------|-------|
| Client ID | `cf749082-77bc-4abd-aea9-92b0d337f38a` |
| Tenant ID | `1d063515-6cad-4195-9486-ea65df456faa` |
| Authority | `https://login.microsoftonline.com/{TENANT_ID}` |
| Scopes | `api://{CLIENT_ID}/user_impersonation` |
| Token type | Bearer (injected in `Authorization` header) |

The user authenticates as **themselves** (user_impersonation scope) — no service account. On first run, a browser window opens for interactive login. Subsequent calls use MSAL's in-memory token cache for silent refresh until the token expires (~1 hour). There is no persistent token cache on disk, so every new process requires a fresh interactive login.

### API Endpoints

| Environment | Base URL |
|-------------|----------|
| **prod** | `https://dkatcpp-a1.corp.lego.com/legotcapi2` |
| **dev** | `https://dkatcpr-a1.corp.lego.com/legotcapi2` |

The base URL is set via `TC_URL` environment variable (auto-configured by `create_tc_session()`).

### TLS / CA Certificate

Corporate hosts (`*.corp.lego.com`) require the LEGO CA bundle — the system trust store does not include it. The session setup automatically sets `REQUESTS_CA_BUNDLE` to `dep-dwf-maya-python/python_externals/team_center_qnetwork/lego_ca/lego_cacert.crt`. Without this, TLS connections hang.

### Key API Patterns

All requests go through `team_center.TeamCenter.Session` (from the sibling dep repo):

| Operation | Method | Path |
|-----------|--------|------|
| Get item by ID | GET | `/items/{TCID}?allrevs` |
| Search items | POST | `/items?ObjType=VME&_pagesize=50` |
| Next page | POST | (uses cursor from previous response) |
| Download spec file | GET | `/items/{TCID}/specifications/mb/vme.mb?Rev={REV}` |
| Get session info | GET | `/session` |
| Search by scheme | POST | `/search/scheme/{scheme}` |
| BOM product | GET | `/boms/product/{id}` |
| Resolve identifier | GET | `/resolver/{id}` |
| Report (3DFLOW) | GET | `{base}/lego/Report/3DFLOW.list.type?argA={type}` (different base path) |

Response data lives under the `_DATA` key. Pagination is handled by `paginate_search()` which calls `search_items` then loops `search_items_next_page` until exhausted.

The converter is a 5-phase pipeline in `main`: authenticate+enumerate → resolve final revisions → init Maya standalone → download `.mb`/convert/cleanup per item → summary. Maya is initialized once and torn down in a `finally`; per-item failures are collected and reported, not fatal.

## Render Manifestation extraction

The `extract_render_manifestation*.py` tools take converted `.ma` VME files and produce standalone Render-only scenes. All four variants share the same core logic:

1. Open `.ma`, find `VME_*` root
2. Delete non-Render styles (Realtime, BIPrint) and their `*_Realtime_*`/`*_BIPrint_*` objectSets
3. Remove unknown nodes (preserving `unknownDag` nodes under `|Connectivity|` — these are ConnectivityTool plugin shapes)
4. Restructure hierarchy (variant-specific)
5. Save as `.ma`

`scripts/maya_convert.py` handles `initialize_maya()` which loads `ConnectivityTool.mll` from the sibling `dep-dwf-maya-python` repo. This is critical — without it, connectivity shape nodes serialize as `unknownDag` and the file cannot be re-saved.

The ADR spec lives at `data/output/ADR-render-manifestation-spec.md` and documents the full Render Manifestation contract: what's included/excluded, all four hierarchy options (A–D), connectivity node types, validation criteria, and vocabulary.

## Key data structures in VME scenes

- **Connectivity** has three sub-hierarchies: `Cbox/` (collision meshes + `Boolean/` cylinder subtractions + `Legacy/` older-format boxes), and `Conn/` (connection fields with typed plugin shape nodes). The ConnectivityTool plugin provides 10 shape node types: `PlanarFieldReceptor`, `PlanarFieldConnector`, `AxleField`, `Cylinder`, `FixedField`, `HingeField`, `WheelField`, `BallField`, `GearField`, `SliderField`.
- **ObjectSets** are organized under `VME_Data_{SuperDesign}` with category sub-sets (EdgeSets, CommonPartSets, SmartSectionSets, FaceSets, OverrideSectionSets) enforced by Partitions.
- **Shading** uses a single `ShadingEngine` (lambert1-based) connecting all Render meshes via `.iog` → `.dsm`. The 18,000+ `materialInfo` nodes are Maya serialization artefacts that must be preserved.

## Conventions

- `--env` selects `prod` (default) or `dev` Team Center hosts; the host mapping lives in the `team_center` lib, not here.
- All scripts share `scripts.logging.setup_logging(level, log_file)` and take `--log-level` / `--log-file`.
- VME selection across tools is consistent: default = all VMEs, or narrow with `--vme-ids` / `--super-designs`.
