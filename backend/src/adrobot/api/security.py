"""One shared token, checked in constant time, and the only thing standing in front of the API.

The two assets worth protecting are the tracker's admin key and the ability to spend
somebody's advertising budget. Neither is protected by a user table, and this service has
one actor — so there is no `sub`, no session store and no JWT, whose `alg` header is a class
of vulnerability that simply never arrives if the token is compared rather than parsed.

**The token travels as a bearer header and not as a cookie.** PLAN-BACKEND §9 allows either;
a cookie is left out because nothing in this design can *set* one — there is no session
endpoint — and a cookie that authenticated a POST would bring CSRF with it, which is the one
class of bug this same-origin, header-authenticated API does not otherwise have. A browser
sends the same header every other client does.

**The check is the test, and this dependency is only its mechanism.** It is declared through
`Security` rather than `Depends` so that every protected operation carries `security` in the
OpenAPI document — which is what
`tests/api/test_security.py::test_no_endpoint_escaped_authentication` reads. An endpoint that
forgets `dependencies=PROTECTED` is a document that says the endpoint is open, and the suite
fails on the document.
"""

from __future__ import annotations

from secrets import compare_digest
from typing import Annotated, Final

from fastapi import Security
from fastapi.security import HTTPBearer
from fastapi.security.http import HTTPAuthorizationCredentials

from adrobot.api.deps import SettingsDep
from adrobot.api.errors import NotAuthenticatedError

_BEARER: Final = HTTPBearer(
    scheme_name="Shared token",
    description="The value of ADROBOT_ACCESS_TOKEN, as `Authorization: Bearer <token>`.",
    # `auto_error=False` so that a missing or malformed header reaches the line below rather
    # than becoming FastAPI's own 403 — which is the wrong status, carries no
    # `WWW-Authenticate` and is the one response of this API that would not be problem+json.
    auto_error=False,
)


async def authenticated(
    settings: SettingsDep,
    presented: Annotated[HTTPAuthorizationCredentials | None, Security(_BEARER)],
) -> None:
    """Refuse the request unless it carried the shared token.

    `compare_digest` and not `==`: the comparison is against a secret, and a short-circuiting
    one leaks its prefix to anybody willing to time it. The token is never bound to a name on
    the way in either — pytest runs with `--showlocals`, and a local holding it would print
    itself into the report of whichever assertion failed nearby.
    """
    if presented is None:
        missing = "this endpoint needs the shared token: send it as `Authorization: Bearer <token>`"
        raise NotAuthenticatedError(missing)
    if not compare_digest(presented.credentials, settings.access_token.get_secret_value()):
        refused = "that is not the shared token this service was started with"
        raise NotAuthenticatedError(refused)


PROTECTED: Final = (Security(authenticated),)
"""What a router that changes anything — or reads anything of somebody's — is built with:
`APIRouter(..., dependencies=PROTECTED)`. A tuple so that a router cannot append to it."""
