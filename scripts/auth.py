"""Azure AD / MSAL authentication for Team Center."""

import logging
import time

try:
    import msal
except ImportError:
    raise ImportError(
        "'msal' package not found. "
        "Ensure python_externals is on sys.path or install via: pip install msal"
    )

logger = logging.getLogger(__name__)

CLIENT_ID = "cf749082-77bc-4abd-aea9-92b0d337f38a"
TENANT_ID = "1d063515-6cad-4195-9486-ea65df456faa"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = [f"api://{CLIENT_ID}/user_impersonation"]


def create_msal_app(client_id=None, authority=None):
    """Create a PublicClientApplication with corporate defaults."""
    return msal.PublicClientApplication(
        client_id=client_id or CLIENT_ID,
        authority=authority or AUTHORITY,
    )


class BearerAuth:
    """requests-compatible auth handler that injects a Bearer token."""

    def __init__(self, msal_app, scopes=None):
        self._app = msal_app
        self._scopes = scopes or SCOPES
        self._token = None
        self._token_valid_until = 0

    def _ensure_token(self):
        if self._token and time.time() < self._token_valid_until:
            return

        token_res = None
        accounts = self._app.get_accounts()
        if accounts:
            token_res = self._app.acquire_token_silent(
                scopes=self._scopes, account=accounts[0]
            )

        if not token_res or "access_token" not in token_res:
            logger.info("Opening browser for interactive authentication...")
            token_res = self._app.acquire_token_interactive(scopes=self._scopes)

        if "access_token" not in token_res:
            raise RuntimeError(
                f"Authentication failed: {token_res.get('error_description', 'unknown error')}"
            )

        self._token = token_res["access_token"]
        self._token_valid_until = time.time() + token_res.get("expires_in", 3600) - 60
        logger.info("Authentication successful.")

    def __call__(self, request):
        self._ensure_token()
        request.headers["Authorization"] = f"Bearer {self._token}"
        return request
