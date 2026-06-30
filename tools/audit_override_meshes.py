"""
Audit all VMEs for Override nodes containing geometry.

Scans .ma files (text parsing, no Maya scene graph) to detect children under the
Overrides hierarchy. For VMEs not yet converted locally, downloads .mb from TC,
converts to .ma in a temp folder, parses, and cleans up immediately.

Output CSV contains ONLY VMEs that have non-empty Overrides.
Progress is tracked in a scan log so the script can resume after a crash.

Run with (text-parse only, no download):
    poetry run python tools/audit_override_meshes.py --skip-download

Run with (download missing VMEs — requires mayapy):
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/audit_override_meshes.py --env prod

Examples:
    poetry run python tools/audit_override_meshes.py --skip-download --output override_audit.csv
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/audit_override_meshes.py --env prod
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/audit_override_meshes.py --vme-ids VX0003001 VX0015362
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/audit_override_meshes.py --env prod --reset
"""

import sys
import os
import argparse
import logging
import csv
import re
import tempfile
import time

# ---------------------------------------------------------------------------
# Path setup: make shared package and corporate deps importable
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.paths import ensure_dep_paths_on_sys_path
ensure_dep_paths_on_sys_path(relative_to=REPO_ROOT)

from scripts.logging import setup_logging

logger = logging.getLogger("override_audit")

# Pattern: any createNode whose parent is "Overrides"
RE_OVERRIDE_CHILD = re.compile(
    r'createNode\s+(\w+)\s+-n\s+"([^"]+)"\s+-p\s+"Overrides"'
)


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------
class ProgressBar:
    """Simple terminal progress bar with ETA."""

    def __init__(self, total):
        self.total = total
        self.current = 0
        self.start_time = time.time()
        self.found_count = 0
        self.error_count = 0
        self._last_line_len = 0

    def update(self, found=False, error=False):
        self.current += 1
        if found:
            self.found_count += 1
        if error:
            self.error_count += 1
        self._render()

    def _render(self):
        elapsed = time.time() - self.start_time
        pct = self.current / self.total if self.total else 1
        bar_width = 30
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)

        if self.current > 0 and pct < 1:
            eta_sec = elapsed / pct * (1 - pct)
            eta_str = self._fmt_time(eta_sec)
        else:
            eta_str = "—"

        elapsed_str = self._fmt_time(elapsed)
        line = (
            f"\r  [{bar}] {self.current}/{self.total} "
            f"({pct:.1%}) | {elapsed_str} elapsed | ETA {eta_str} | "
            f"found: {self.found_count} | errors: {self.error_count}"
        )
        pad = max(0, self._last_line_len - len(line))
        sys.stderr.write(line + " " * pad)
        sys.stderr.flush()
        self._last_line_len = len(line)

    def finish(self):
        self._render()
        sys.stderr.write("\n")
        sys.stderr.flush()

    @staticmethod
    def _fmt_time(seconds):
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        m, s = divmod(seconds, 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"


# ---------------------------------------------------------------------------
# Scan log — tracks which VMEs have been processed (for resume)
# ---------------------------------------------------------------------------
def load_scan_log(log_path):
    """Load set of already-scanned VME keys from the scan log."""
    scanned = set()
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    scanned.add(line)
    return scanned


def append_scan_log(log_file, key):
    """Append a scanned VME key to the log file."""
    log_file.write(key + "\n")
    log_file.flush()


# ---------------------------------------------------------------------------
# Text-based .ma parser
# ---------------------------------------------------------------------------
def parse_override_children(ma_path):
    """Read .ma as text and return list of (node_type, node_name) under Overrides."""
    text = open(ma_path, "r", encoding="utf-8", errors="replace").read()
    return RE_OVERRIDE_CHILD.findall(text)


# ---------------------------------------------------------------------------
# CSV loading (reuses same format as pipeline_full_corpus)
# ---------------------------------------------------------------------------
def load_csv(csv_path):
    """Read input CSV and return list of (vme_id, rev, superdesign) tuples."""
    items = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vme_id = row.get("TCID", "").strip()
            rev = row.get("REV", "").strip()
            superdesign = row.get("SUPERDESIGN", "").strip()
            if vme_id:
                items.append((vme_id, rev, superdesign))
    return items


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Audit VMEs for Override nodes containing geometry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--env",
        choices=["dev", "prod"],
        default="prod",
        help="Team Center environment (default: prod)",
    )
    parser.add_argument(
        "--csv-input",
        default=os.path.join(REPO_ROOT, "data", "input", "3DFLOW-list-type-active.csv"),
        help="Path to VME list CSV (default: data/input/3DFLOW-list-type-active.csv)",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(REPO_ROOT, "data", "output", "override_audit.csv"),
        help="Output CSV path (default: data/output/override_audit.csv)",
    )
    parser.add_argument(
        "--scan-log",
        default=os.path.join(REPO_ROOT, "data", "output", "override_audit_scanlog.txt"),
        help="Scan progress log for resume (default: data/output/override_audit_scanlog.txt)",
    )
    parser.add_argument(
        "--error-log",
        default=os.path.join(REPO_ROOT, "data", "output", "override_audit_errors.csv"),
        help="Error log CSV (default: data/output/override_audit_errors.csv)",
    )
    parser.add_argument(
        "--vme-dir",
        default=os.path.join(REPO_ROOT, "data", "output", "VME"),
        help="Directory with already-converted .ma files (default: data/output/VME/)",
    )
    parser.add_argument(
        "--temp-dir",
        default=os.path.join(tempfile.gettempdir(), "DWF", "override_audit"),
        help="Temp directory for downloads (default: %%TEMP%%/DWF/override_audit/)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Only scan existing .ma files; skip TC download for missing VMEs",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear scan log and output CSV, start fresh",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Process VME list in reverse order (start from the end)",
    )
    parser.add_argument(
        "--vme-ids",
        nargs="+",
        help="Process only these VME IDs (overrides CSV input)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument("--log-file", default=None)
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    setup_logging(args.log_level, args.log_file, logger_name="override_audit")

    # --- Load VME list ---
    if args.vme_ids:
        items = [(vid, "", "") for vid in args.vme_ids]
        logger.info(f"Processing {len(items)} VME(s) from --vme-ids")
    else:
        items = load_csv(args.csv_input)
        logger.info(f"Loaded {len(items)} VMEs from {args.csv_input}")

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    # --- Reverse order if requested ---
    if args.reverse:
        items = list(reversed(items))
        logger.info("Processing in reverse order")

    # --- Handle reset ---
    if args.reset:
        for path in (args.output, args.scan_log, args.error_log):
            if os.path.isfile(path):
                os.remove(path)
                logger.info(f"Removed {path}")

    # --- Load scan log for resume ---
    already_scanned = load_scan_log(args.scan_log)
    if already_scanned:
        logger.info(f"Resuming: {len(already_scanned)} VMEs already scanned, skipping those")

    # --- Open output CSV (append if resuming, write header if new) ---
    csv_is_new = not os.path.isfile(args.output) or os.path.getsize(args.output) == 0
    csv_file = open(args.output, "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    if csv_is_new:
        writer.writerow(["TCID", "REV", "SUPERDESIGN", "OVERRIDE_CHILDREN"])
        csv_file.flush()

    # --- Open scan log (append mode) ---
    scan_log_file = open(args.scan_log, "a", encoding="utf-8")

    # --- Open error log CSV (append if resuming, write header if new) ---
    error_is_new = not os.path.isfile(args.error_log) or os.path.getsize(args.error_log) == 0
    error_file = open(args.error_log, "a", newline="", encoding="utf-8")
    error_writer = csv.writer(error_file)
    if error_is_new:
        error_writer.writerow(["TCID", "REV", "SUPERDESIGN", "ERROR"])
        error_file.flush()

    # --- Prepare download infrastructure (lazy) ---
    session = None
    maya_initialized = False

    def ensure_session():
        nonlocal session
        if session is None:
            from scripts.teamcenter import create_tc_session
            session = create_tc_session(args.env)
            logger.info(f"TC session created ({args.env})")
        return session

    def ensure_maya():
        nonlocal maya_initialized
        if not maya_initialized:
            from scripts.maya_convert import initialize_maya
            initialize_maya()
            maya_initialized = True
            logger.info("Maya standalone initialized")

    # --- Process each VME ---
    hit_count = 0
    scanned_count = 0
    skipped_count = 0
    error_count = 0

    # Filter out already-scanned items for progress bar total
    remaining = [(v, r, s) for (v, r, s) in items if f"{v}_{r}" not in already_scanned]
    logger.info(f"To process: {len(remaining)} VMEs ({len(items) - len(remaining)} already done)")

    progress = ProgressBar(len(remaining))

    try:
        for idx, (vme_id, rev, superdesign) in enumerate(remaining, 1):
            vme_key = f"{vme_id}_{rev}"
            ma_filename = f"{vme_key}.ma" if rev else f"{vme_id}.ma"
            ma_path = os.path.join(args.vme_dir, ma_filename)

            # Try existing .ma first
            if os.path.isfile(ma_path):
                try:
                    children = parse_override_children(ma_path)
                    child_names = [name for (_type, name) in children]
                    scanned_count += 1
                    if child_names:
                        hit_count += 1
                        writer.writerow([vme_id, rev, superdesign, ";".join(child_names)])
                        csv_file.flush()
                        logger.info(f"  FOUND: {vme_key} → {child_names}")
                    append_scan_log(scan_log_file, vme_key)
                    progress.update(found=bool(child_names))
                except Exception as e:
                    error_count += 1
                    append_scan_log(scan_log_file, vme_key)
                    error_writer.writerow([vme_id, rev, superdesign, str(e)[:200]])
                    error_file.flush()
                    logger.error(f"  ERROR parsing {vme_key}: {e}")
                    progress.update(error=True)
                continue

            # Need to download + convert
            if args.skip_download:
                skipped_count += 1
                progress.update()
                continue

            # Download .mb → convert .ma → parse → cleanup
            try:
                ensure_session()
                ensure_maya()

                from scripts.vme import download_vme_mb
                from scripts.maya_convert import convert_mb_to_ma

                os.makedirs(args.temp_dir, exist_ok=True)
                mb_path = download_vme_mb(session, vme_id, rev, args.temp_dir)
                temp_ma_path = os.path.join(args.temp_dir, ma_filename)

                convert_mb_to_ma(mb_path, temp_ma_path)
                children = parse_override_children(temp_ma_path)
                child_names = [name for (_type, name) in children]
                scanned_count += 1

                if child_names:
                    hit_count += 1
                    writer.writerow([vme_id, rev, superdesign, ";".join(child_names)])
                    csv_file.flush()
                    logger.info(f"  FOUND: {vme_key} → {child_names}")

                append_scan_log(scan_log_file, vme_key)
                progress.update(found=bool(child_names))

                # Cleanup temp files immediately
                for p in (mb_path, temp_ma_path):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

            except Exception as e:
                error_count += 1
                append_scan_log(scan_log_file, vme_key)
                error_writer.writerow([vme_id, rev, superdesign, str(e)[:200]])
                error_file.flush()
                logger.error(f"  ERROR: {vme_key} — {e}")
                progress.update(error=True)

    finally:
        progress.finish()
        csv_file.close()
        scan_log_file.close()
        error_file.close()
        if maya_initialized:
            from scripts.maya_convert import uninitialize_maya
            uninitialize_maya()

    logger.info(f"Results written to {args.output}")
    logger.info(f"Scan log at {args.scan_log}")
    logger.info(
        f"Summary: {hit_count} VMEs with overrides out of {scanned_count} scanned | "
        f"{skipped_count} skipped | {error_count} errors"
    )


if __name__ == "__main__":
    main()
