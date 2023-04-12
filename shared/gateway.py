from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
import httpx
import os
import logging

# API Gateway pattern — single entry point routes to the right microservice.
# Django: one URL router for the whole app. Here: gateway dispatches by prefix.
# In production: use Kong, Traefik, or AWS API Gateway. This is the educational version.
# Key responsibility: auth token forwarding, request routing, response passthrough.

logger = logging.getLogger(__name__)

SERVICES = {
    "users":         os.environ.get("USERS_URL",         "http://localhost:8001"),
    "orders":        os.environ.get("ORDERS_URL",        "http://localhost:8002"),
    "notifications": os.environ.get("NOTIFICATIONS_URL", "http://localhost:8003"),
}

app = FastAPI(title="API Gateway", version="0.1.0")

# Route table: path prefix -> service name
ROUTES: list[tuple[str, str]] = [
    ("/users",         "users"),
    ("/token",         "users"),
    ("/orders",        "orders"),
    ("/notifications", "notifications"),
]


def _resolve(path: str) -> str | None:
    for prefix, service in ROUTES:
        if path.startswith(prefix):
            return service
    return None


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    full_path = f"/{path}"
    service   = _resolve(full_path)
    if not service:
        raise HTTPException(404, f"no route for {full_path!r}")

    target_url = f"{SERVICES[service]}{full_path}"
    headers    = dict(request.headers)
    headers.pop("host", None)  # Don't forward the gateway's host header

    body = await request.body()

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )
        except httpx.ConnectError:
            raise HTTPException(503, f"service {service!r} unavailable")
        except httpx.TimeoutException:
            raise HTTPException(504, f"service {service!r} timed out")

    logger.info("[gateway] %s %s -> %s (%d)", request.method, full_path, service, resp.status_code)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway", "routes": len(ROUTES)}
