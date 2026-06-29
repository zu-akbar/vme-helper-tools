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


def create_tc_session(environment, *, client_id=None, authority=None, scopes=None, timeout=120, dryrun=False):
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
        environment=environment, authenticate=auth, timeout=timeout, dryrun=dryrun
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


# ---------------------------------------------------------------------------
# Extended API wrappers (endpoints not directly on Session)
# ---------------------------------------------------------------------------


def get_session_info(session):
    """GET /session."""
    url = session.build_url(path_elements=["session"])
    return session._get(url)


def search_items_paginated(session, entries, *, page_size=50, sort=None, all_files=False, references=None):
    """Paginating item search with sort/allfiles/references support."""
    query_elements = {}
    if sort:
        query_elements["_sort"] = sort
    if all_files:
        query_elements["allfiles"] = None
    if references:
        query_elements["References"] = references

    search_body = {}
    if entries:
        search_body["entries"] = entries

    return paginate_search(session, search_body, page_size, query_elements or None)


def search_scheme(session, scheme, fields, *, page_size=None):
    """POST /search/scheme/{scheme}."""
    query_elements = {}
    if page_size:
        query_elements["_pagesize"] = str(page_size)
    url = session.build_url(
        path_elements=["search", "scheme", scheme],
        query_elements=query_elements or None,
    )
    return session._post(url, {"_DATA": fields})


def get_bom_product(session, product_id, *, explode=False):
    """GET /boms/product/{id}."""
    query_elements = {"explode": None} if explode else None
    url = session.build_url(
        path_elements=["boms", "product", str(product_id)],
        query_elements=query_elements,
    )
    return session._get(url)


def get_masterdata(session):
    """GET /masterdata."""
    url = session.build_url(path_elements=["masterdata"])
    return session._get(url)


def get_search_scheme_definition(session, scheme):
    """GET /search/scheme/{scheme} — returns the scheme definition."""
    url = session.build_url(path_elements=["search", "scheme", scheme])
    return session._get(url)


def resolve_identifier(session, identifier):
    """GET /resolver/{id}."""
    url = session.build_url(path_elements=["resolver", str(identifier)])
    return session._get(url)


def get_report_elements_by_type(session, element_type):
    """GET /lego/Report/3DFLOW.list.type?argA={type}.

    Uses /lego/ base path instead of /legotcapi2/.
    """
    tc_url = os.environ.get("TC_URL", "")
    base = tc_url.rsplit("/legotcapi2", 1)[0] if "/legotcapi2" in tc_url else tc_url.rsplit("/", 1)[0]
    report_url = f"{base}/lego/Report/3DFLOW.list.type?argA={element_type}"
    response = session.session.get(report_url, timeout=session.timeout)
    session._raise_if_response_not_ok(response, report_url)
    try:
        return response.json()
    except ValueError:
        return {"_RAW": response.text}
