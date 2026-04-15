"""
mci_api.py — MineColab Improved v2
FastAPI control server: REST endpoints + WebSocket log streaming.
Verifies Bearer token on every request.
Registers tunnel URL with the light node on startup.
"""

import asyncio
import logging
import time
from typing import Optional

import requests as http_requests
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

logger = logging.getLogger("mci_api")
security = HTTPBearer(auto_error=False)


# ─────────────────────────────────────────────
#  Request / Response models
# ─────────────────────────────────────────────

class CommandRequest(BaseModel):
    command: str


class RegisterRequest(BaseModel):
    token: str
    url: str


# ─────────────────────────────────────────────
#  App factory
# ─────────────────────────────────────────────

def create_app(
    minecraft_server,           # MinecraftServer instance from mci_core
    api_token: str,
    lightnode_url: str = "",
    panel_base_url: str = "",
) -> FastAPI:

    app = FastAPI(
        title="MCI Control API",
        version="2.0.0",
        description="MineColab Improved — remote control API",
        docs_url="/docs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Shared async queue for WebSocket log streaming
    log_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
    minecraft_server.set_async_queue(log_queue)

    # Active WebSocket connections
    ws_clients: list[WebSocket] = []
    _start_time = time.time()

    # ── Token verification ────────────────────

    def verify_token(
        creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> str:
        if creds is None or creds.credentials != api_token:
            raise HTTPException(status_code=401, detail="Invalid or missing token")
        return creds.credentials

    # ── Background: broadcast logs to WebSockets ─

    async def _log_broadcaster():
        while True:
            line: str = await log_queue.get()
            dead = []
            for ws in list(ws_clients):
                try:
                    await ws.send_text(line)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                if ws in ws_clients:
                    ws_clients.remove(ws)

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(_log_broadcaster())
        # Register tunnel URL with lightnode if configured
        if lightnode_url:
            asyncio.create_task(_register_on_start())

    async def _register_on_start():
        # Give cloudflared a moment to settle
        await asyncio.sleep(3)
        # The agent writes the tunnel URL to /tmp/mci_tunnel_url.txt
        try:
            with open("/tmp/mci_tunnel_url.txt") as f:
                tunnel_url = f.read().strip()
            if tunnel_url:
                _register_lightnode(tunnel_url)
        except Exception:
            pass

    def _register_lightnode(tunnel_url: str):
        if not lightnode_url:
            return
        try:
            r = http_requests.post(
                f"{lightnode_url}/register",
                json={"token": api_token, "url": tunnel_url},
                timeout=10,
            )
            logger.info("Lightnode registration: %s", r.status_code)
        except Exception as e:
            logger.warning("Lightnode registration failed: %s", e)

    # ─────────────────────────────────────────
    #  REST Endpoints
    # ─────────────────────────────────────────

    @app.get("/")
    def root():
        return {"service": "MCI API v2", "uptime": round(time.time() - _start_time)}

    @app.get("/health")
    def health():
        """Public health-check (no token required)."""
        return {"ok": True}

    @app.get("/status")
    def get_status(_tok: str = Depends(verify_token)):
        """Return Minecraft server status + player count."""
        data = minecraft_server.get_status()
        data["api_uptime"] = round(time.time() - _start_time)
        return data

    @app.post("/command")
    def send_command(req: CommandRequest, _tok: str = Depends(verify_token)):
        """Send a console command to the Minecraft server."""
        if minecraft_server.status not in ("running", "starting"):
            raise HTTPException(status_code=503, detail="Server is not running")
        ok = minecraft_server.send_command(req.command)
        return {"success": ok, "command": req.command}

    @app.get("/logs")
    def get_logs(lines: int = 200, _tok: str = Depends(verify_token)):
        """Return the latest N log lines from the ring buffer."""
        return {"logs": minecraft_server.log_buffer[-lines:]}

    @app.post("/start")
    def start_server(_tok: str = Depends(verify_token)):
        """(Re)start the Minecraft server."""
        if minecraft_server.status in ("running", "starting"):
            return {"success": False, "message": "Already running"}
        ok = minecraft_server.start()
        return {"success": ok}

    @app.post("/stop")
    def stop_server(_tok: str = Depends(verify_token)):
        """Gracefully stop the Minecraft server."""
        minecraft_server.stop()
        return {"success": True}

    @app.post("/backup")
    def backup_world(_tok: str = Depends(verify_token)):
        """Trigger a world backup to Drive."""
        try:
            path = minecraft_server.backup_world()
            return {"success": True, "backup_path": path}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/sync")
    def sync_to_drive(_tok: str = Depends(verify_token)):
        """Force-sync local disk → Google Drive."""
        try:
            minecraft_server.sync_to_drive()
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ─────────────────────────────────────────
    #  WebSocket: live log stream
    # ─────────────────────────────────────────

    @app.websocket("/ws/logs")
    async def ws_logs(websocket: WebSocket):
        """
        Connect with ?token=<your_token>
        Sends real-time log lines as plain text frames.
        """
        token_param = websocket.query_params.get("token", "")
        if token_param != api_token:
            await websocket.close(code=4001, reason="Invalid token")
            return

        await websocket.accept()
        ws_clients.append(websocket)

        # Replay the last 100 lines immediately
        for line in minecraft_server.log_buffer[-100:]:
            try:
                await websocket.send_text(line)
            except Exception:
                break

        try:
            while True:
                # Keep connection alive; actual data is pushed by broadcaster
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Echo back any ping frames from client
                if msg == "ping":
                    await websocket.send_text("pong")
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        finally:
            if websocket in ws_clients:
                ws_clients.remove(websocket)

    # ── Internal: re-register tunnel URL ─────

    @app.post("/internal/update-tunnel")
    def update_tunnel(req: RegisterRequest):
        """
        Called by mci_agent when the cloudflared URL changes.
        Internal only — protected by token in body.
        """
        if req.token != api_token:
            raise HTTPException(status_code=401, detail="Invalid token")
        with open("/tmp/mci_tunnel_url.txt", "w") as f:
            f.write(req.url)
        _register_lightnode(req.url)
        return {"success": True, "url": req.url}

    return app
