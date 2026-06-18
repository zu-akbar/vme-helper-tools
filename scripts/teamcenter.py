"""Team Center session creation and generic helpers."""

import logging

from scripts.auth import BearerAuth, create_msal_app, SCOPES

logger = logging.getLogger(__name__)


def create_tc_session(environment, *, client_id=None, authority=None, scopes=None, timeout=120):
    """Create an authenticated Team Center session.

    Requires team_center.TeamCenter.Session to be importable (via ensure_dep_paths_on_sys_path).
    """
    from team_center.TeamCenter import Session as TeamCenterSession

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
