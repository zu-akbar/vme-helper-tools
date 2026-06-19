"""VME enumeration, revision resolution, and file download from Team Center."""

import logging
import os

from team_center.TeamCenter import RequestFailed
from team_center.TeamCenterFormats import revision_sort_key

from scripts.teamcenter import paginate_search

logger = logging.getLogger(__name__)


def fetch_all_vmes(session, page_size=50):
    """Retrieve all VME items from Team Center with pagination."""
    logger.info("Searching for all VMEs in Team Center...")
    items = paginate_search(
        session,
        search_body={},
        page_size=page_size,
        query_elements={"ObjType": "VME"},
    )
    logger.info(f"Total VMEs found: {len(items)}")
    return items


def fetch_vmes_by_ids(session, vme_ids):
    """Retrieve specific VME items by their IDs."""
    items = []
    for vme_id in vme_ids:
        try:
            response = session.get_item(
                tcid=vme_id, query_elements={"allrevs": None}
            )
            item_data = response.get("_DATA", {})
            if isinstance(item_data, dict) and vme_id in item_data:
                rev_key = next(iter(item_data[vme_id]))
                item = item_data[vme_id][rev_key]
                item["ItemId"] = vme_id
                if "_ALLREVS" not in item and "_ALLREVS" in response:
                    item["_ALLREVS"] = response["_ALLREVS"]
                items.append(item)
            elif isinstance(item_data, list):
                items.extend(item_data)
            else:
                items.append(response)
        except RequestFailed as e:
            logger.error(f"Failed to fetch VME {vme_id}: {e}")
    return items


def fetch_vmes_by_super_designs(session, super_design_ids, page_size=50):
    """Retrieve VMEs matching given SuperDesign IDs."""
    items = []
    for sd_id in super_design_ids:
        try:
            found = paginate_search(
                session,
                search_body={},
                page_size=page_size,
                query_elements={
                    "ObjType": "VME",
                    "SuperDesign": str(sd_id),
                },
            )
            logger.info(f"  SuperDesign {sd_id}: {len(found)} VME(s)")
            items.extend(found)
        except RequestFailed as e:
            logger.error(f"Failed to search for SuperDesign {sd_id}: {e}")
    return items


def get_final_revision(item):
    """Determine the final (latest released) revision of a VME item.

    Returns the revision string (e.g., "B") or None if no released revision exists.
    """
    allrevs = item.get("_ALLREVS", [])
    if not allrevs:
        rev = item.get("Rev")
        status = item.get("ReleaseStatus", "")
        if rev and status != "Working":
            return rev
        return None

    released = [r for r in allrevs if r.get("ReleaseStatus") != "Working"]
    if not released:
        return None

    released.sort(key=lambda r: revision_sort_key(r["Rev"]), reverse=True)
    return released[0]["Rev"]


def download_vme_mb(session, vme_id, rev, output_dir):
    """Download the .mb file for a VME from Team Center.

    Returns the path to the downloaded file.
    """
    uri = f"/items/{vme_id}/specifications/mb/vme.mb"
    if rev:
        uri += f"?Rev={rev}"

    output_file = os.path.join(output_dir, f"{vme_id}_{rev}.mb")
    session.download_file(uri, output_file)
    return output_file
