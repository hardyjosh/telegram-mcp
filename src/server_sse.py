"""
SSE/HTTP transport wrapper for the Telegram MCP server.

This allows the server to be accessed remotely by Claude clients
via Server-Sent Events (SSE) over HTTPS instead of stdio.

FastMCP 1.22.0+ has built-in SSE support via sse_app() which returns
a Starlette application. This wrapper adds:
- Bearer token authentication
- Health check endpoint
- Landing page with setup instructions
"""

import os
import sys
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
import uvicorn

# Add parent directory to path to import main module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the existing MCP server and Telegram client
from main import mcp, client

# ============================================================================
# Configuration
# ============================================================================

BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN")
PORT = int(os.environ.get("PORT", 8000))
HOST = os.environ.get("HOST", "0.0.0.0")

# OAuth Client Credentials (set these in .env for authentication)
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET")


# ============================================================================
# Authentication Middleware
# ============================================================================

# Global set to store valid OAuth tokens
VALID_TOKENS: set = set()


class BearerAuthMiddleware:
    """Middleware to require bearer token authentication on MCP endpoints."""

    # Paths that don't require authentication
    PUBLIC_PATHS = [
        "/health",
        "/.well-known/",
        "/authorize",
        "/token",
    ]

    def __init__(self, app, protected_paths: list[str]):
        self.app = app
        self.protected_paths = protected_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            method = scope.get("method", "GET")

            # Allow CORS preflight requests through without auth
            if method == "OPTIONS":
                await self.app(scope, receive, send)
                return

            # Allow public paths through without auth
            if any(path.startswith(p) for p in self.PUBLIC_PATHS):
                await self.app(scope, receive, send)
                return

            # Everything else requires auth (MCP endpoints at /, /sse, /mcp, /messages)
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()

            if not auth_header.startswith("Bearer "):
                response = JSONResponse(
                    {"error": "Missing or invalid Authorization header"}, status_code=401
                )
                await response(scope, receive, send)
                return

            token = auth_header[7:]  # Remove "Bearer " prefix

            # Check if token is valid (either from OAuth flow or static BEARER_TOKEN)
            is_valid = token in VALID_TOKENS or (BEARER_TOKEN and token == BEARER_TOKEN)

            if not is_valid:
                response = JSONResponse({"error": "Invalid token"}, status_code=401)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


# ============================================================================
# Health & Info Endpoints
# ============================================================================


async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for load balancers and monitoring."""
    # Check if Telegram client is connected
    try:
        connected = client.is_connected()
    except Exception:
        connected = False

    return JSONResponse(
        {
            "status": "ok" if connected else "degraded",
            "service": "telegram-mcp",
            "telegram_connected": connected,
            "auth_mode": "bearer" if BEARER_TOKEN else "none",
        }
    )


# ============================================================================
# OAuth Endpoints (for Claude connector discovery)
# ============================================================================


async def oauth_metadata(request: Request) -> JSONResponse:
    """
    OAuth 2.0 Authorization Server Metadata.
    Required for Claude to discover auth endpoints.
    """
    base_url = str(request.base_url).rstrip("/")
    # Only force HTTPS if coming through a proxy (X-Forwarded-Proto) or tunnel
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto == "https":
        base_url = base_url.replace("http://", "https://")

    return JSONResponse(
        {
            "issuer": base_url,
            "authorization_endpoint": f"{base_url}/authorize",
            "token_endpoint": f"{base_url}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
        }
    )


async def authorize_endpoint(request: Request):
    """
    Authorization endpoint for OAuth flow.
    For authless mode, immediately redirects back with a code.
    """
    redirect_uri = request.query_params.get("redirect_uri")
    state = request.query_params.get("state", "")

    if redirect_uri:
        # Redirect back with dummy code
        separator = "&" if "?" in redirect_uri else "?"
        return HTMLResponse(
            status_code=302,
            headers={"Location": f"{redirect_uri}{separator}code=authless-code&state={state}"},
        )

    return JSONResponse({"error": "redirect_uri required"}, status_code=400)


async def token_endpoint(request: Request) -> JSONResponse:
    """
    Token endpoint - exchanges authorization code for access token.
    Validates OAuth Client ID and Secret if configured.

    Supports credentials via:
    1. HTTP Basic Auth header (RFC 6749 standard)
    2. Form body parameters (alternative)
    """
    import secrets
    import base64

    # Parse the request body (form-encoded)
    client_id = ""
    client_secret = ""
    code = ""
    grant_type = ""

    try:
        form = await request.form()
        client_id = form.get("client_id", "")
        client_secret = form.get("client_secret", "")
        code = form.get("code", "")
        grant_type = form.get("grant_type", "")
    except Exception:
        # Try JSON body as fallback
        try:
            body = await request.json()
            client_id = body.get("client_id", "")
            client_secret = body.get("client_secret", "")
            code = body.get("code", "")
            grant_type = body.get("grant_type", "")
        except Exception:
            pass

    # Check for HTTP Basic Auth header (RFC 6749 Section 2.3.1)
    # Format: "Basic base64(client_id:client_secret)"
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Basic "):
        try:
            encoded = auth_header[6:]  # Remove "Basic " prefix
            decoded = base64.b64decode(encoded).decode("utf-8")
            if ":" in decoded:
                header_client_id, header_client_secret = decoded.split(":", 1)
                # Use header values if body values are empty
                if not client_id:
                    client_id = header_client_id
                if not client_secret:
                    client_secret = header_client_secret
        except Exception:
            pass  # Fall back to form body credentials

    # If OAuth credentials are configured, validate them
    if OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET:
        if client_id != OAUTH_CLIENT_ID:
            return JSONResponse(
                {"error": "invalid_client", "error_description": "Invalid client_id"},
                status_code=401,
            )
        if client_secret != OAUTH_CLIENT_SECRET:
            return JSONResponse(
                {"error": "invalid_client", "error_description": "Invalid client_secret"},
                status_code=401,
            )

    # Generate a real access token (random, secure)
    access_token = secrets.token_urlsafe(32)

    # Store the token for validation (in-memory for now)
    # In production, you'd use Redis or a database
    global VALID_TOKENS
    if "VALID_TOKENS" not in globals():
        VALID_TOKENS = set()
    VALID_TOKENS.add(access_token)

    return JSONResponse(
        {"access_token": access_token, "token_type": "Bearer", "expires_in": 86400}
    )


async def info_page(request: Request) -> HTMLResponse:
    """Landing page with setup instructions."""
    return HTMLResponse(
        f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram MCP Server</title>
        <style>
            body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 700px; margin: 50px auto; padding: 20px; line-height: 1.6; }}
            code {{ background: #f4f4f4; padding: 2px 8px; border-radius: 4px; font-size: 0.9em; }}
            pre {{ background: #f4f4f4; padding: 16px; border-radius: 8px; overflow-x: auto; }}
            .success {{ color: #22c55e; font-weight: 600; }}
            .warning {{ color: #f59e0b; }}
            h2 {{ margin-top: 2em; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.5em; }}
            ul {{ padding-left: 1.5em; }}
        </style>
    </head>
    <body>
        <h1>Telegram MCP Server</h1>
        <p class="success">Server is running</p>

        <h2>Add to Claude</h2>
        <ol>
            <li>Go to <strong>claude.ai → Settings → Connectors</strong></li>
            <li>Click <strong>"Add Custom Connector"</strong></li>
            <li>Enter this URL: <code id="url"></code></li>
            {"<li>Configure the bearer token in your client headers</li>" if BEARER_TOKEN else ""}
        </ol>

        <h2>Endpoints</h2>
        <ul>
            <li><code>/health</code> - Health check</li>
            <li><code>/sse</code> - SSE endpoint (MCP protocol)</li>
            <li><code>/messages/</code> - Message endpoint (MCP protocol)</li>
        </ul>

        <h2>Available Tools</h2>
        <p>This server exposes <strong>85 Telegram tools</strong> including:</p>
        <ul>
            <li>Messaging (send, edit, delete, forward, reply, pin, schedule)</li>
            <li>Media (download/upload photos, videos, documents, voice)</li>
            <li>Groups (create, manage members, permissions, ban, promote)</li>
            <li>Channels (create, post, manage subscribers)</li>
            <li>Contacts (add, delete, import, block, unblock)</li>
            <li>Search (global search with filters)</li>
            <li>And much more...</li>
        </ul>

        <p class="warning">Auth mode: <strong>{"Bearer Token" if BEARER_TOKEN else "None (open)"}</strong></p>

        <script>
            document.getElementById('url').textContent = window.location.origin;
        </script>
    </body>
    </html>
    """
    )


# ============================================================================
# Application Setup
# ============================================================================


@asynccontextmanager
async def lifespan(app):
    """Lifespan context manager to start/stop Telegram client with uvicorn's event loop."""
    # Initialise permissions
    import permissions as perms
    perms.init_db()

    # Build permission map from registered tool annotations
    tools_info = []
    for tool_name, tool in mcp._tool_manager._tools.items():
        annotations = {}
        if tool.annotations:
            annotations = {
                "readOnlyHint": getattr(tool.annotations, "readOnlyHint", False),
                "destructiveHint": getattr(tool.annotations, "destructiveHint", False),
            }
        tools_info.append({"name": tool_name, "annotations": annotations})
    perms.build_tool_permission_map(tools_info)
    write_tools = [t["name"] for t in tools_info if t["annotations"].get("destructiveHint")]
    print(f"Permission map built: {len(perms.TOOL_PERMISSION_MAP)} tools mapped")

    # Patch tool_manager.call_tool to enforce permissions on ALL tool calls
    _original_call_tool = mcp._tool_manager.call_tool

    async def _checked_call_tool(name, arguments, **kwargs):
        chat_id = arguments.get("chat_id") or arguments.get("group_id")
        allowed, reason = perms.check_permission(name, chat_id)
        if not allowed:
            # Return as a plain string — same as what tool functions return
            return f"Permission denied: {reason}"
        return await _original_call_tool(name, arguments, **kwargs)

    mcp._tool_manager.call_tool = _checked_call_tool

    print("Starting Telegram client...")
    await client.start()
    print("Telegram client connected.")
    yield
    # Cleanup on shutdown
    print("Disconnecting Telegram client...")
    await client.disconnect()
    print("Telegram client disconnected.")


def create_app_with_lifespan() -> Starlette:
    """Create the SSE application with lifespan handler."""
    # Get the built-in apps from FastMCP
    sse_app = mcp.sse_app()
    streamable_http_app = mcp.streamable_http_app()

    # Create wrapper app with additional routes
    # Mount sse_app at root - it handles /sse and /messages internally
    routes = [
        Route("/health", health_check),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Route("/.well-known/oauth-protected-resource", oauth_metadata),
        Route("/authorize", authorize_endpoint),
        Route("/token", token_endpoint, methods=["POST"]),
        # SSE app handles both /sse and /messages/ endpoints
        # Mount at root so paths work correctly
        Mount("/", app=sse_app),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)

    # Only require auth if OAuth credentials are configured
    # Protect MCP endpoints but NOT OAuth/health endpoints
    if OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET:
        app = BearerAuthMiddleware(
            app,
            protected_paths=["/sse", "/messages", "/mcp"],
            # Note: Root "/" is also MCP but handled by exclude logic below
        )

    return app


def main():
    """Main entry point for SSE server."""
    auth_mode = (
        "OAuth (Client ID/Secret)"
        if (OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET)
        else "None (INSECURE!)"
    )
    print(f"Starting Telegram MCP Server (SSE mode)")
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Auth: {auth_mode}")
    if not (OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET):
        print(
            f"  WARNING: No authentication configured! Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET"
        )
    print()

    # Create app with lifespan (Telegram client starts in uvicorn's event loop)
    app = create_app_with_lifespan()

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
