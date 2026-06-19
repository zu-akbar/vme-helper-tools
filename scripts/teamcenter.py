"""Team Center session creation and generic helpers."""

import logging
import os

from scripts.auth import BearerAuth, create_msal_app, SCOPES
from scripts.paths import resolve_dep_paths

logger = logging.getLogger(__name__)


TC_URLS = {
    "prod": "https://dkatcpp-a1.corp.lego.com/legotcapi2",
    "dev": "https://dkatcpr-a1.corp.lego.com/legotcapi2",
}


def _ensure_lego_ca_bundle():
    """Point requests at the LEGO CA certificate bundle shipped in dep-dwf-maya-python.

    Without this, TLS connections to *.corp.lego.com hang (the system trust store
    does not include the corporate CA).  ved_tool sets this via its
    team_center_qnetwork.Network import; we replicate that here.
    """
    if os.environ.get("REQUESTS_CA_BUNDLE"):
        return

    try:
        python_externals, _ = resolve_dep_paths()
    except FileNotFoundError:
        return

    ca_path = os.path.join(
        python_externals, "team_center_qnetwork", "lego_ca", "lego_cacert.crt"
    )
    if os.path.isfile(ca_path):
        os.environ["REQUESTS_CA_BUNDLE"] = ca_path
        logger.debug(f"REQUESTS_CA_BUNDLE set to: {ca_path}")


def create_tc_session(environment, *, client_id=None, authority=None, scopes=None, timeout=120):
    """Create an authenticated Team Center session.

    Requires team_center.TeamCenter.Session to be importable (via ensure_dep_paths_on_sys_path).
    """
    from team_center.TeamCenter import Session as TeamCenterSession

    _ensure_lego_ca_bundle()

    if not os.environ.get("TC_URL") and environment in TC_URLS:
        os.environ["TC_URL"] = TC_URLS[environment]
        logger.debug(f"TC_URL set to: {TC_URLS[environment]}")

    msal_app = create_msal_app(client_id=client_id, authority=authority)
    auth = BearerAuth(msal_app, scopes=scopes or SCOPES)
    session = TeamCenterSession(
        environment=environment, authenticate=auth, timeout=timeout
    )
    logger.info(f"Connected to Team Center ({environment}) as: {session.get_username()}")
    return session


def paginate_search(session, search_body, page_size, query_elements):
    """Generic paginating search over Team Center. Returns all items across pages."""
    page = session.search_items(
        search_body=search_body,
        page_size=page_size,
        query_elements=query_elements,
    )
    all_items = page.get("_DATA", [])
    page_num = 1
    logger.info(f"  Page {page_num}: {len(all_items)} items")

    while True:
        page = session.search_items_next_page(page)
        if not page:
            break
        page_num += 1
        items = page.get("_DATA", [])
        logger.info(f"  Page {page_num}: {len(items)} items")
        all_items.extend(items)

    return all_items
