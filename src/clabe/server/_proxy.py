"""Forwarding a request to another clabe server (a run, a daemon), swapping the caller's token for the target's.

The daemon proxies its run under ``/run/``, and the hub each rig's daemon under ``/rigs/<id>/``; responses (including
Server-Sent Events) are streamed back as they come.
"""

import hmac

import httpx
from fastapi import Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

#: Hop-by-hop headers (and the caller's credentials), which a proxy must not forward.
HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
     "transfer-encoding", "upgrade", "host", "content-length", "authorization"}
)  # fmt: skip


def given_token(authorization: str | None, token_param: str | None) -> str | None:
    """The token a request carries, from ``Authorization: Bearer`` or ``?token=``."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return token_param


def token_check(token: str | None):
    """A dependency rejecting requests without ``token`` (when one is set)."""

    async def check(
        authorization: str | None = Header(default=None),
        token_param: str | None = Query(default=None, alias="token"),
    ) -> None:
        """Raises 401 unless the request carries the token."""
        if token is None:
            return
        given = given_token(authorization, token_param)
        if given is None or not hmac.compare_digest(given, token):
            raise HTTPException(status_code=401, detail="A valid token is required.")

    return check


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
