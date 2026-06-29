"""
Team Center REST API CLI — unified access to all TC endpoints.

Run with:
    poetry run python tools/tc_api.py <command> [options]

Examples:
    python tools/tc_api.py auth --env dev
    python tools/tc_api.py items get VX0003001 --allrevs
    python tools/tc_api.py items search --type VME --sort -ModifiedDate --page-size 10
    python tools/tc_api.py items search --type VME --format csv
    python tools/tc_api.py items refs VX0003626
    python tools/tc_api.py bom get 50075309 --explode
    python tools/tc_api.py recipes get 6448586
    python tools/tc_api.py materials get VX0003001
    python tools/tc_api.py resolver 11206886
    python tools/tc_api.py magic VX0003001 A
    python tools/tc_api.py report --type decoration
"""

import sys
import os
import argparse
import json
import logging

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.paths import ensure_dep_paths_on_sys_path
ensure_dep_paths_on_sys_path(relative_to=REPO_ROOT)

from team_center.TeamCenter import RequestFailed

from scripts.logging import setup_logging
from scripts.teamcenter import (
    create_tc_session,
    paginate_search,
    get_session_info,
    search_items_paginated,
    search_scheme,
    get_bom_product,
    get_masterdata,
    get_search_scheme_definition,
    resolve_identifier,
    get_report_elements_by_type,
)
from scripts.tc_output import format_json, format_csv, write_output

logger = logging.getLogger("tc_api")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_auth(session, args):
    return {"username": session.get_username(), "status": "authenticated"}


def cmd_session(session, args):
    return get_session_info(session)


def cmd_items_get(session, args):
    query_elements = {}
    if args.allrevs:
        query_elements["allrevs"] = None
    if args.allfiles:
        query_elements["allfiles"] = None
    return session.get_item(tcid=args.tcid, query_elements=query_elements or None)


def cmd_items_search(session, args):
    entries = []
    if args.type:
        entries.append({"field": "ObjType", "values": [args.type]})
    if args.field:
        for f in args.field:
            if "=" in f:
                k, v = f.split("=", 1)
                entries.append({"field": k, "values": [v]})
            else:
                entries.append({"field": f})

    return search_items_paginated(
        session,
        entries,
        page_size=args.page_size,
        sort=args.sort,
        all_files=args.allfiles,
        references=args.references,
    )


def cmd_items_refs(session, args):
    return session.search_references_as_item_list(
        rtcid=args.tcid, query_elements=None
    )


def cmd_items_checkout(session, args):
    return session.checkout(args.tcids)


def cmd_items_checkin(session, args):
    return session.checkin(args.tcids)


def cmd_items_revise(session, args):
    return session.revise_items(args.tcids)


def cmd_items_set_refs(session, args):
    refs = []
    for r in args.ref:
        parts = r.split(":")
        if len(parts) == 3:
            tcid, rev, name = parts
            refs.append({"URI": f"/items/{tcid}?Rev={rev}", "ReferenceName": name})
        elif len(parts) == 2:
            tcid, name = parts
            refs.append({"URI": f"/items/{tcid}", "ReferenceName": name})
        else:
            logger.error(f"Invalid --ref format: {r} (expected TCID:REV:NAME or TCID:NAME)")
            sys.exit(1)
    return session.set_references(args.tcid, refs)


def cmd_items_props(session, args):
    if args.json_file:
        with open(args.json_file, "r", encoding="utf-8") as f:
            props = json.load(f)
    elif args.prop:
        props = {}
        for p in args.prop:
            k, v = p.split("=", 1)
            props[k] = v
    else:
        logger.error("Provide --prop K=V or --json-file")
        sys.exit(1)
    return session.set_properties(args.tcid, props)


def cmd_items_create(session, args):
    body = None
    if args.json_file:
        with open(args.json_file, "r", encoding="utf-8") as f:
            body = json.load(f)
    return session.create_item(args.item_type, args.name, body=body)


def cmd_items_workflow(session, args):
    return session.trigger_workflow(args.workflow, args.tcids)


def cmd_items_baseline(session, args):
    return session.baseline_items(args.tcids, args.baseline)


def cmd_items_release(session, args):
    return session.release_items(args.tcids, args.release)


def cmd_bom_get(session, args):
    return get_bom_product(session, args.product_id, explode=args.explode)


def cmd_bom_search(session, args):
    fields = []
    if args.field:
        for f in args.field:
            if "=" in f:
                k, v = f.split("=", 1)
                fields.append({"field": k, "values": [v]})
            else:
                fields.append({"field": f})
    return search_scheme(session, args.scheme, fields, page_size=args.page_size)


def cmd_recipes_get(session, args):
    return session.get_recipe(args.material_no)


def cmd_recipes_update(session, args):
    with open(args.json_file, "r", encoding="utf-8") as f:
        recipe = json.load(f)
    return session.set_recipe(args.material_no, recipe)


def cmd_recipes_delete(session, args):
    return session.delete_uri(f"recipes/{args.material_no}")


def cmd_materials_get(session, args):
    return session.get_materials(args.tcid, key="TCID")


def cmd_masterdata(session, args):
    return get_masterdata(session)


def cmd_search_scheme(session, args):
    return get_search_scheme_definition(session, args.scheme)


def cmd_search_run(session, args):
    fields = []
    if args.field:
        for f in args.field:
            if "=" in f:
                k, v = f.split("=", 1)
                fields.append({"field": k, "values": [v]})
            else:
                fields.append({"field": f})
    return search_scheme(session, args.scheme, fields, page_size=args.page_size)


def cmd_resolver(session, args):
    return resolve_identifier(session, args.identifier)


def cmd_magic(session, args):
    return session.get_magic_hash(args.tcid, args.rev)


def cmd_report(session, args):
    return get_report_elements_by_type(session, args.type)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def _common_parent():
    """Parent parser with shared args (inherited by all leaf subcommands)."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--env", choices=["dev", "prod"], default="prod",
                   help="Team Center environment (default: prod)")
    p.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   default="INFO", help="Logging verbosity (default: INFO)")
    p.add_argument("--log-file", metavar="PATH", help="Also write log output to this file")
    p.add_argument("--output", metavar="PATH", help="Write result to file instead of stdout")
    p.add_argument("--format", choices=["json", "csv"], default="json",
                   help="Output format (default: json)")
    p.add_argument("--page-size", type=int, default=50, help="Search page size (default: 50)")
    p.add_argument("--timeout", type=int, default=120, help="Request timeout in seconds (default: 120)")
    p.add_argument("--dry-run", action="store_true", help="Skip write operations")
    return p


def build_parser():
    common = _common_parent()

    parser = argparse.ArgumentParser(
        prog="tc_api",
        description="Team Center REST API CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- auth ---
    p = subparsers.add_parser("auth", parents=[common], help="Verify authentication")
    p.set_defaults(func=cmd_auth)

    # --- session ---
    p = subparsers.add_parser("session", parents=[common], help="Show session info")
    p.set_defaults(func=cmd_session)

    # --- items ---
    items_parser = subparsers.add_parser("items", help="Item operations")
    items_sub = items_parser.add_subparsers(dest="items_cmd")

    # items get
    p = items_sub.add_parser("get", parents=[common], help="Get item by TCID")
    p.add_argument("tcid", help="Item TCID (e.g. VX0003001)")
    p.add_argument("--allrevs", action="store_true", help="Include all revisions")
    p.add_argument("--allfiles", action="store_true", help="Include file metadata")
    p.set_defaults(func=cmd_items_get)

    # items search
    p = items_sub.add_parser("search", parents=[common], help="Search items")
    p.add_argument("--type", help="Object type (VME, VFM, decoration, stickerSheet, BI)")
    p.add_argument("--field", action="append", metavar="K=V", help="Search field (repeatable)")
    p.add_argument("--sort", help="Sort expression (e.g. -ModifiedDate)")
    p.add_argument("--allfiles", action="store_true", help="Include file metadata")
    p.add_argument("--references", metavar="TCID", help="Find items referencing this TCID")
    p.set_defaults(func=cmd_items_search)

    # items refs
    p = items_sub.add_parser("refs", parents=[common], help="Find items referencing a TCID")
    p.add_argument("tcid", help="Item TCID to find references for")
    p.set_defaults(func=cmd_items_refs)

    # items checkout
    p = items_sub.add_parser("checkout", parents=[common], help="Checkout items")
    p.add_argument("tcids", nargs="+", help="TCIDs to checkout")
    p.set_defaults(func=cmd_items_checkout)

    # items checkin
    p = items_sub.add_parser("checkin", parents=[common], help="Checkin items")
    p.add_argument("tcids", nargs="+", help="TCIDs to checkin")
    p.set_defaults(func=cmd_items_checkin)

    # items revise
    p = items_sub.add_parser("revise", parents=[common], help="Revise items")
    p.add_argument("tcids", nargs="+", help="TCIDs to revise")
    p.set_defaults(func=cmd_items_revise)

    # items set-refs
    p = items_sub.add_parser("set-refs", parents=[common], help="Set reference relationships")
    p.add_argument("tcid", help="Item TCID to set references on")
    p.add_argument("--ref", action="append", required=True, metavar="TCID:REV:NAME",
                   help="Reference in format TCID:REV:NAME or TCID:NAME (repeatable)")
    p.set_defaults(func=cmd_items_set_refs)

    # items props
    p = items_sub.add_parser("props", parents=[common], help="Update item properties")
    p.add_argument("tcid", help="Item TCID")
    p.add_argument("--prop", action="append", metavar="K=V", help="Property to set (repeatable)")
    p.add_argument("--json-file", metavar="PATH", help="JSON file with properties body")
    p.set_defaults(func=cmd_items_props)

    # items create
    p = items_sub.add_parser("create", parents=[common], help="Create item")
    p.add_argument("item_type", help="Item type (e.g. decoration)")
    p.add_argument("name", help="Item name")
    p.add_argument("--json-file", metavar="PATH", help="JSON file with additional body")
    p.set_defaults(func=cmd_items_create)

    # items workflow
    p = items_sub.add_parser("workflow", parents=[common], help="Trigger workflow")
    p.add_argument("workflow", help="Workflow name (e.g. LE7_RENDER_WORKFLOW)")
    p.add_argument("tcids", nargs="+", help="TCIDs to run workflow on")
    p.set_defaults(func=cmd_items_workflow)

    # items baseline
    p = items_sub.add_parser("baseline", parents=[common], help="Set baseline")
    p.add_argument("baseline", help="Baseline name (e.g. LE7_PRELIM_2)")
    p.add_argument("tcids", nargs="+", help="TCIDs to baseline")
    p.set_defaults(func=cmd_items_baseline)

    # items release
    p = items_sub.add_parser("release", parents=[common], help="Release items")
    p.add_argument("release", help="Release name (e.g. LE7_FINAL)")
    p.add_argument("tcids", nargs="+", help="TCIDs to release")
    p.set_defaults(func=cmd_items_release)

    # --- bom ---
    bom_parser = subparsers.add_parser("bom", help="BOM operations")
    bom_sub = bom_parser.add_subparsers(dest="bom_cmd")

    # bom get
    p = bom_sub.add_parser("get", parents=[common], help="Get BOM for product")
    p.add_argument("product_id", help="Product/material number")
    p.add_argument("--explode", action="store_true", help="Explode BOM tree")
    p.set_defaults(func=cmd_bom_get)

    # bom search
    p = bom_sub.add_parser("search", parents=[common], help="Search using scheme")
    p.add_argument("scheme", help="Search scheme (e.g. Y950)")
    p.add_argument("--field", action="append", metavar="K=V", help="Search field (repeatable)")
    p.set_defaults(func=cmd_bom_search)

    # --- recipes ---
    recipes_parser = subparsers.add_parser("recipes", help="Recipe operations")
    recipes_sub = recipes_parser.add_subparsers(dest="recipes_cmd")

    # recipes get
    p = recipes_sub.add_parser("get", parents=[common], help="Get recipe")
    p.add_argument("material_no", help="Material number / recipe ID")
    p.set_defaults(func=cmd_recipes_get)

    # recipes update
    p = recipes_sub.add_parser("update", parents=[common], help="Update recipe")
    p.add_argument("material_no", help="Material number / recipe ID")
    p.add_argument("--json-file", required=True, metavar="PATH", help="JSON file with recipe body")
    p.set_defaults(func=cmd_recipes_update)

    # recipes delete
    p = recipes_sub.add_parser("delete", parents=[common], help="Delete recipe")
    p.add_argument("material_no", help="Material number / recipe ID")
    p.set_defaults(func=cmd_recipes_delete)

    # --- materials ---
    p = subparsers.add_parser("materials", parents=[common], help="Get material by TCID")
    p.add_argument("tcid", help="Material TCID (e.g. VX0003001)")
    p.set_defaults(func=cmd_materials_get)

    # --- masterdata ---
    p = subparsers.add_parser("masterdata", parents=[common], help="Get master data")
    p.set_defaults(func=cmd_masterdata)

    # --- search ---
    search_parser = subparsers.add_parser("search", help="Search scheme operations")
    search_sub = search_parser.add_subparsers(dest="search_cmd")

    # search scheme
    p = search_sub.add_parser("scheme", parents=[common], help="Get search scheme definition")
    p.add_argument("scheme", help="Scheme name (e.g. Y950)")
    p.set_defaults(func=cmd_search_scheme)

    # search run
    p = search_sub.add_parser("run", parents=[common], help="Execute search using scheme")
    p.add_argument("scheme", help="Scheme name (e.g. Y950, IR_FILES2)")
    p.add_argument("--field", action="append", metavar="K=V", help="Search field (repeatable)")
    p.set_defaults(func=cmd_search_run)

    # --- resolver ---
    p = subparsers.add_parser("resolver", parents=[common], help="Resolve identifier")
    p.add_argument("identifier", help="Identifier to resolve (e.g. design ID)")
    p.set_defaults(func=cmd_resolver)

    # --- magic ---
    p = subparsers.add_parser("magic", parents=[common], help="Get magic hash")
    p.add_argument("tcid", help="Item TCID")
    p.add_argument("rev", help="Revision (e.g. A)")
    p.set_defaults(func=cmd_magic)

    # --- report ---
    p = subparsers.add_parser("report", parents=[common], help="List elements by type")
    p.add_argument("--type", required=True, help="Element type (e.g. decoration)")
    p.set_defaults(func=cmd_report)

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.log_level, args.log_file)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    try:
        session = create_tc_session(args.env, timeout=args.timeout, dryrun=args.dry_run)
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        sys.exit(1)

    try:
        result = args.func(session, args)
    except RequestFailed as e:
        logger.error(f"TC request failed: {e}")
        sys.exit(1)

    if result is not None:
        if args.format == "csv" and isinstance(result, list):
            content = format_csv(result)
        else:
            content = format_json(result)
        write_output(content, args.output)


if __name__ == "__main__":
    main()
