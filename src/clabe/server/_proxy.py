"""Forwarding a request to another clabe server (a run, a daemon), swapping the caller's token for the target's.

The daemon proxies its run under ``/run/``, and the hub each rig's daemon under ``/rigs/<id>/``; responses (including
Server-Sent Events) are streamed back as they come.
"""

import hmac
import json
from urllib.parse import parse_qsl, quote, urlencode

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: Hop-by-hop headers (and the caller's credentials), which a proxy must not forward.
HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
     "transfer-encoding", "upgrade", "host", "content-length", "authorization", "cookie"}
)  # fmt: skip
#: How long the browser keeps a token it signed in with.
COOKIE_MAX_AGE = 30 * 24 * 3600


def cookie_name(host: str | None) -> str:
    """The token cookie of the server at ``host`` (``rig-3:24070``). Cookies ignore ports, so the port is in the
    name: a hub and a daemon on one machine keep their own."""
    port = (host or "").rpartition(":")[2] if ":" in (host or "") else "80"
    return f"clabe_token_{port if port.isdigit() else '80'}"


def given_token(request: Request) -> str | None:
    """The token a request carries: ``Authorization: Bearer``, ``?token=``, or the browser's cookie."""
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return request.query_params.get("token") or request.cookies.get(cookie_name(request.headers.get("host")))


def valid(given: str | None, token: str | None) -> bool:
    """Whether ``given`` is ``token`` (anything is, when no token is set)."""
    return token is None or (given is not None and hmac.compare_digest(given.encode(), token.encode()))


def token_check(token: str | None):
    """A dependency rejecting requests without ``token`` (when one is set)."""

    async def check(request: Request) -> None:
        """Raises 401 unless the request carries the token."""
        if not valid(given_token(request), token):
            raise HTTPException(status_code=401, detail="A valid token is required.")

    return check


class RequireToken:
    """Refuses requests under ``prefix`` without the token (401), for apps mounted without the API's dependency
    (the hub's MCP server)."""

    def __init__(self, app: ASGIApp, token: str | None, prefix: str) -> None:
        self.app = app
        self.token = token
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] == "http"
            and self.token is not None
            and (path == self.prefix or path.startswith(self.prefix + "/"))
        ) and not valid(given_token(Request(scope, receive)), self.token):
            await JSONResponse({"detail": "A valid token is required."}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def relative_redirect(path: str, query: str = "", *, slash: bool = False, status_code: int = 307) -> Response:
    """A redirect relative to the address asked for, so it holds behind a proxy that serves clabe under a prefix.

    The location is ``path``'s last segment (``./`` when ``path`` ends in ``/``); with ``slash``, that segment with a
    trailing slash: ``/rigs/rig-3`` -> ``./rig-3/``, which a browser at ``/clabe/rigs/rig-3`` resolves to
    ``/clabe/rigs/rig-3/``.
    """
    location = "./" + quote(path.rsplit("/", 1)[-1]) + ("/" if slash else "")
    return RedirectResponse(location + (f"?{query}" if query else ""), status_code=status_code)


class TokenCookies:
    """Lets a browser hold the token in a cookie (HttpOnly) rather than in its address bar.

    - ``POST …/api/login`` ``{"token": "..."}`` checks the token and sets the cookie (what the page's sign-in box
      sends); ``POST …/api/logout`` clears it. Any prefix works (``/rigs/rig-3/run/api/login``): it is this server's.
    - A page opened with ``?token=`` (a link clabe printed) is redirected to the same address without it, the cookie
      set: bookmarks, history and shared links never carry the token.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        path, method = scope["path"], scope["method"]
        response: Response | None = None
        if method == "POST" and path.endswith("/api/login"):
            response = await self._login(request)
        elif method == "POST" and path.endswith("/api/logout"):
            response = JSONResponse({"ok": True})
            response.delete_cookie(cookie_name(request.headers.get("host")), path="/")
        elif method == "GET" and "/api/" not in path and "token" in request.query_params and self.token is not None:
            given = request.query_params["token"]
            if valid(given, self.token):
                query = urlencode(
                    [(k, v) for k, v in parse_qsl(scope.get("query_string", b"").decode()) if k != "token"]
                )
                response = relative_redirect(path, query, status_code=303)
                self._set(response, request, given)
        if response is None:
            await self.app(scope, receive, send)
        else:
            await response(scope, receive, send)

    async def _login(self, request: Request) -> Response:
        """Checks the token sent and keeps it in the cookie."""
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = {}
        given = body.get("token") if isinstance(body, dict) else None
        if self.token is None:
            return JSONResponse({"ok": True, "required": False})
        if not isinstance(given, str) or not valid(given.strip(), self.token):
            return JSONResponse({"ok": False, "detail": "That token is not right."}, status_code=401)
        response = JSONResponse({"ok": True, "required": True})
        self._set(response, request, given.strip())
        return response

    @staticmethod
    def _set(response: Response, request: Request, token: str) -> None:
        """Keeps ``token`` in the browser's cookie for this server (HttpOnly, 30 days)."""
        response.set_cookie(
            cookie_name(request.headers.get("host")), token, max_age=COOKIE_MAX_AGE, path="/", httponly=True,
            samesite="lax",
        )  # fmt: skip


async def forward(
    client: httpx.AsyncClient, request: Request, url: str, token: str | None, *, unreachable: str
) -> Response:
    """Sends ``request`` to ``url`` with ``token`` (instead of the caller's), and streams the answer back.

    Args:
        client: The proxy's HTTP client.
        request: The incoming request.
        url: Where to send it (without query; the request's query is kept, minus its ``token``).
        token: The target's token, if it needs one.
        unreachable: The message when the target does not answer (502).
    """
    query = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    upstream = client.build_request(request.method, url, params=query, headers=headers, content=await request.body())
    try:
        response = await client.send(upstream, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"{unreachable}: {exc}") from exc
    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in HOP_HEADERS},
        background=BackgroundTask(response.aclose),
    )
