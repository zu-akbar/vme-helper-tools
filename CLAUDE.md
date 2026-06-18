# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Tooling for automating Team Center (LEGO PLM) workflows around VMEs — enumerating them, exporting their revision metadata, and batch-converting their Maya binary files to ASCII. Two layers: an importable `scripts/` package holding shared infrastructure (auth, TC session, paths, logging), and `tools/` holding the runnable CLI entry points.

## Commands

```bash
poetry install                              # set up the in-project .venv

# Export VME revision metadata to CSV (no Maya needed)
poetry run python tools/export_vme_list.py --env prod --output vme_list.csv

# Batch download .mb and convert to .ma — MUST run under Maya's interpreter
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --env prod --output-dir ./output
& "C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe" tools/fetch_and_convert_ma.py --dry-run   # plan only, no auth-to-Maya cost
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

The converter is a 5-phase pipeline in `main`: authenticate+enumerate → resolve final revisions → init Maya standalone → download `.mb`/convert/cleanup per item → summary. Maya is initialized once and torn down in a `finally`; per-item failures are collected and reported, not fatal.

## Conventions

- `--env` selects `prod` (default) or `dev` Team Center hosts; the host mapping lives in the `team_center` lib, not here.
- All scripts share `scripts.logging.setup_logging(level, log_file)` and take `--log-level` / `--log-file`.
- VME selection across tools is consistent: default = all VMEs, or narrow with `--vme-ids` / `--super-designs`.
