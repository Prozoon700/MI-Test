import asyncio
import base64
import json
import logging
import mimetypes
import os
import shutil
import time
from io import BytesIO
from pathlib import Path
from typing import Optional
from zipfile import ZIP_DEFLATED, ZipFile

import requests as http_req
from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from mci_core import build_jvm_flags, normalize_jvm_mem

log = logging.getLogger("mci_api")
security = HTTPBearer(auto_error=False)

CONFIG_FILE = ".mci-config.json"
LEGACY_CONFIG_FILE = ".mci_config.json"
SERVER_LIST_FILE = "server_list.txt"

TUNNEL_SERVICES = {
    "argo": {
        "label": "Cloudflare Tunnel",
        "description": "No requiere token y expone el puerto mediante TryCloudflare.",
        "docs_url": "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/",
        "extra_fields": [],
        "server_list_key": None,
    },
    "playit": {
        "label": "Playit.gg",
        "description": "Crea un tunnel TCP persistente con un agente sencillo.",
        "docs_url": "https://playit.gg/account/agents/new-docker",
        "extra_fields": [{"key": "secretkey", "label": "Secret Key", "type": "password"}],
        "server_list_key": "playit_proxy",
    },
    "ngrok": {
        "label": "Ngrok",
        "description": "Tunnel TCP popular con token y region configurable.",
        "docs_url": "https://dashboard.ngrok.com/get-started/your-authtoken",
        "extra_fields": [
            {"key": "authtoken", "label": "Auth Token", "type": "password"},
            {"key": "region", "label": "Region", "type": "text"},
        ],
        "server_list_key": "ngrok_proxy",
    },
    "zrok": {
        "label": "Zrok",
        "description": "Servicio OpenZiti para compartir tuneles bajo demanda.",
        "docs_url": "https://zrok.io",
        "extra_fields": [{"key": "authtoken", "label": "Token", "type": "password"}],
        "server_list_key": "zrok_proxy",
    },
    "localtonet": {
        "label": "LocaltoNet",
        "description": "Tunnel TCP sencillo con user token.",
        "docs_url": "https://localtonet.com/usertoken",
        "extra_fields": [{"key": "authtoken", "label": "User Token", "type": "password"}],
        "server_list_key": "localtonet_proxy",
    },
    "tailscale": {
        "label": "Tailscale",
        "description": "Red privada mesh; requiere clave del nodo o auth key.",
        "docs_url": "https://login.tailscale.com/admin/settings/keys",
        "extra_fields": [{"key": "authtoken", "label": "Auth Key", "type": "password"}],
        "server_list_key": "tailscale_proxy",
    },
}

SOFTWARE_OPTIONS = {
    "paper": {"label": "Paper", "advanced_versions": ["latest", "stable"]},
    "purpur": {"label": "Purpur", "advanced_versions": ["latest", "stable"]},
    "folia": {"label": "Folia", "advanced_versions": ["latest", "stable"], "min_mc": "1.20.1"},
    "fabric": {"label": "Fabric", "advanced_versions": ["latest-loader", "stable-loader"]},
    "forge": {"label": "Forge", "advanced_versions": ["recommended", "latest"]},
    "neoforge": {"label": "NeoForge", "advanced_versions": ["recommended", "latest"], "min_mc": "1.20.2"},
    "velocity": {"label": "Velocity", "advanced_versions": ["latest", "stable"]},
}


class CmdReq(BaseModel):
    command: str


class PropsReq(BaseModel):
    properties: dict


class StartupReq(BaseModel):
    jvm_mem: Optional[str] = "4G"
    flag_preset: Optional[str] = "auto"
    custom_args: Optional[str] = ""
    tunnel_service: Optional[str] = "argo"
    sync_interval: Optional[int] = 300
    language: Optional[str] = None


class SelectReq(BaseModel):
    server: str


class RegReq(BaseModel):
    token: str
    url: str


class RenameReq(BaseModel):
    path: str
    new_name: str
    allow_extension_change: bool = False


class LangReq(BaseModel):
    language: str = Field(pattern="^(en|es)$")


class IconReq(BaseModel):
    image_data: str


class CreateServerReq(BaseModel):
    name: str
    mc_version: str
    server_type: str
    software_build: Optional[str] = "latest"
    tunnel_service: str = "argo"
    tunnel_config: dict = Field(default_factory=dict)
    jvm_mem: Optional[str] = "4G"
    max_players: Optional[int] = 20
    motd: Optional[str] = ""
    advanced_mode: bool = False
    language: Optional[str] = "en"


def create_app(mc, api_token: str, drive_path: str, lightnode_url: str = "", panel_url: str = "") -> FastAPI:
    app = FastAPI(title="MCI API", version="3.0.0", docs_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    log_queue: asyncio.Queue = asyncio.Queue(maxsize=8000)
    mc.set_async_queue(log_queue)
    ws_clients: list = []
    boot_time = time.time()
    activity_log: list = []
    drive = Path(drive_path)

    def verify(creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
        if not creds or creds.credentials != api_token:
            raise HTTPException(401, "Invalid token")
        return creds.credentials

    def _server_root(name: Optional[str] = None) -> Path:
        server_name = name or mc.server_name
        return drive / server_name

    def _config_path() -> Path:
        modern = drive / CONFIG_FILE
        legacy = drive / LEGACY_CONFIG_FILE
        return modern if modern.exists() or not legacy.exists() else legacy

    def _read_global_config() -> dict:
        path = _config_path()
        if not path.exists():
            return {"language": "en"}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"language": "en"}

    def _write_global_config(data: dict):
        path = drive / CONFIG_FILE
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _read_server_list() -> dict:
        path = drive / SERVER_LIST_FILE
        if not path.exists():
            return {"server_list": [], "server_in_use": ""}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"server_list": [], "server_in_use": ""}

    def _write_server_list(data: dict):
        (drive / SERVER_LIST_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _read_colabconfig(name: Optional[str] = None) -> dict:
        path = _server_root(name) / "colabconfig.txt"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_colabconfig(data: dict, name: Optional[str] = None):
        path = _server_root(name) / "colabconfig.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _log_act(kind: str, description: str):
        activity_log.append({"type": kind, "description": description, "ts": int(time.time())})
        activity_log[:] = activity_log[-500:]

    async def _broadcaster():
        while True:
            line = await log_queue.get()
            for ws in list(ws_clients):
                try:
                    await ws.send_text(line)
                except Exception:
                    if ws in ws_clients:
                        ws_clients.remove(ws)

    async def _flush_queue():
        await asyncio.sleep(6)
        if not lightnode_url:
            return
        try:
            response = http_req.get(f"{lightnode_url}?action=flush&token={api_token}", timeout=10)
            for change in response.json().get("changes", []):
                action = change.get("action")
                data = change.get("data", {})
                if action == "/properties":
                    _write_props(mc.server_name, data)
                elif action == "/startup":
                    _save_startup_config(StartupReq(**data))
        except Exception:
            pass

    def _read_props(name: str) -> dict:
        sp = _server_root(name) / "server.properties"
        if not sp.exists():
            return {}
        props = {}
        for line in sp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                props[key.strip()] = value.strip()
        return props

    def _write_props(name: str, props: dict):
        sp = _server_root(name) / "server.properties"
        existing = _read_props(name)
        existing.update({k: str(v) for k, v in props.items()})
        lines = [f"{key}={value}" for key, value in sorted(existing.items())]
        sp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True

    def _save_startup_config(req: StartupReq):
        cc = _read_colabconfig(mc.server_name)
        cc.update(
            {
                "tunnel_service": req.tunnel_service,
                "jvm_mem": normalize_jvm_mem(req.jvm_mem or cc.get("jvm_mem", "4G")),
                "flag_preset": req.flag_preset,
                "custom_jvm_args": req.custom_args,
                "sync_interval": int(req.sync_interval or 300),
            }
        )
        if req.flag_preset and req.flag_preset != "custom":
            cc["custom_jvm_args"] = build_jvm_flags(cc.get("server_type", mc.server_type), cc["jvm_mem"])
        if req.language:
            cfg = _read_global_config()
            cfg["language"] = req.language
            _write_global_config(cfg)
        _write_colabconfig(cc, mc.server_name)

    def _safe_path(path_str: str, root: Optional[Path] = None) -> Path:
        base = (root or _server_root()).resolve()
        candidate = (base / path_str.lstrip("/")).resolve()
        if not str(candidate).startswith(str(base)):
            raise HTTPException(403, "Access denied")
        return candidate

    def _sync_after_change():
        try:
            mc.sync_to_drive()
        except Exception:
            pass

    def _rename_allowed(old_name: str, new_name: str, allow_extension_change: bool) -> bool:
        if allow_extension_change:
            return True
        return Path(old_name).suffix.lower() == Path(new_name).suffix.lower()

    def _backup_root() -> Path:
        return drive / "backup" / "world"

    def _list_backups():
        base = _backup_root()
        if not base.exists():
            return []
        items = []
        for item in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not item.exists():
                continue
            size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file()) if item.is_dir() else item.stat().st_size
            items.append(
                {
                    "name": item.name,
                    "path": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": _fmt_size(size),
                    "size_bytes": size,
                    "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(item.stat().st_mtime)),
                }
            )
        return items

    @app.on_event("startup")
    async def _start():
        asyncio.create_task(_broadcaster())
        asyncio.create_task(_flush_queue())

    @app.get("/health")
    def health():
        return {"ok": True, "language": _read_global_config().get("language", "en")}

    @app.get("/meta", dependencies=[Depends(verify)])
    def meta():
        cfg = _read_global_config()
        server_list = _read_server_list()
        token_presence = {}
        for key, info in TUNNEL_SERVICES.items():
            list_key = info.get("server_list_key")
            token_presence[key] = bool(list_key and server_list.get(list_key))
        return {
            "language": cfg.get("language", "en"),
            "software": SOFTWARE_OPTIONS,
            "tunnels": TUNNEL_SERVICES,
            "advanced_file_extensions": False,
            "saved_tunnel_tokens": token_presence,
            "config_file": CONFIG_FILE,
        }

    @app.get("/status", dependencies=[Depends(verify)])
    def status():
        data = mc.get_status()
        data["api_uptime"] = round(time.time() - boot_time)
        try:
            stat = os.statvfs(str(drive))
            data["disk_used"] = f"{round((stat.f_blocks - stat.f_bfree) * stat.f_frsize / 1e9, 1)} GB"
        except Exception:
            pass
        data["language"] = _read_global_config().get("language", "en")
        data["icon_url"] = "/settings/icon" if data.get("has_icon") else ""
        return data

    @app.get("/servers", dependencies=[Depends(verify)])
    def list_servers():
        sc = _read_server_list()
        servers = []
        current_status = mc.get_status()
        for name in sc.get("server_list", []):
            root = drive / name
            if not root.exists():
                continue
            cc = _read_colabconfig(name)
            props = _read_props(name)
            is_current = mc.server_name == name
            status = current_status if is_current else None
            servers.append(
                {
                    "name": name,
                    "status": status["status"] if status else "offline",
                    "server_type": cc.get("server_type", ""),
                    "version": cc.get("server_version", ""),
                    "software_build": cc.get("software_build", "latest"),
                    "players": status.get("players_online", 0) if status else 0,
                    "max_players": int(props.get("max-players", "20") or "20"),
                    "memory_used": status.get("memory_used") if status else None,
                    "memory_max": status.get("memory_max") if status else cc.get("jvm_mem", "4").replace("G", ""),
                    "uptime": status.get("uptime_seconds", 0) if status else 0,
                    "motd": status.get("motd") if status else props.get("motd", ""),
                    "tunnel_service": cc.get("tunnel_service", "argo"),
                }
            )
        return {"servers": servers, "current": sc.get("server_in_use", "")}

    @app.post("/servers/select", dependencies=[Depends(verify)])
    def select_server(req: SelectReq):
        sc = _read_server_list()
        if req.server not in sc.get("server_list", []):
            raise HTTPException(404, "Server not found")
        sc["server_in_use"] = req.server
        _write_server_list(sc)
        return {"success": True}

    @app.post("/servers/create", dependencies=[Depends(verify)])
    def create_server(req: CreateServerReq):
        server_name = req.name.strip()
        if not server_name:
            raise HTTPException(400, "Missing server name")
        if any(ch in server_name for ch in r'\/:*?"<>|'):
            raise HTTPException(400, "Invalid server name")
        root = drive / server_name
        if root.exists():
            raise HTTPException(409, "Server already exists")

        sc = _read_server_list()
        root.mkdir(parents=True, exist_ok=False)
        motd = req.motd or f"{server_name} running on MCI"
        props = {
            "motd": motd,
            "server-port": "25565",
            "max-players": str(req.max_players or 20),
            "online-mode": "true",
            "enable-command-block": "false",
            "pvp": "true",
        }
        _write_props(server_name, props)
        cc = {
            "server_name": server_name,
            "server_type": req.server_type,
            "server_version": req.mc_version,
            "software_build": req.software_build or "latest",
            "tunnel_service": req.tunnel_service,
            "jvm_mem": normalize_jvm_mem(req.jvm_mem or "4G"),
            "flag_preset": "auto",
            "custom_jvm_args": build_jvm_flags(req.server_type, req.jvm_mem or "4G"),
            "sync_interval": 300,
            "language": req.language or "en",
            "advanced_mode": req.advanced_mode,
        }
        _write_colabconfig(cc, server_name)
        (root / "eula.txt").write_text("eula=true\n", encoding="utf-8")

        tunnel_info = TUNNEL_SERVICES.get(req.tunnel_service, {})
        tunnel_key = tunnel_info.get("server_list_key")
        if tunnel_key and req.tunnel_config:
            sc.setdefault(tunnel_key, {}).update(req.tunnel_config)

        sc.setdefault("server_list", [])
        sc["server_list"].append(server_name)
        if not sc.get("server_in_use"):
            sc["server_in_use"] = server_name
        _write_server_list(sc)

        cfg = _read_global_config()
        cfg["language"] = req.language or cfg.get("language", "en")
        _write_global_config(cfg)
        _log_act("create", f"Servidor creado: {server_name}")
        return {"success": True, "server": server_name}

    @app.post("/command", dependencies=[Depends(verify)])
    def command(req: CmdReq):
        if mc.status not in ("running", "starting"):
            raise HTTPException(503, "Server not running")
        ok = mc.send_command(req.command)
        _log_act("command", req.command)
        return {"success": ok, "command": req.command}

    @app.get("/logs", dependencies=[Depends(verify)])
    def get_logs(lines: int = Query(200, ge=1, le=2000), since: int = Query(0)):
        buf = mc.log_buffer
        if since > 0 and since < len(buf):
            buf = buf[since:]
        else:
            buf = buf[-lines:]
        return {"logs": buf, "total": len(mc.log_buffer)}

    @app.post("/start", dependencies=[Depends(verify)])
    def start():
        if mc.status in ("running", "starting"):
            return {"success": False, "message": "Server already running"}
        ok = mc.start()
        _log_act("start", "Servidor iniciado")
        return {"success": ok}

    @app.post("/stop", dependencies=[Depends(verify)])
    def stop():
        mc.stop()
        _log_act("stop", "Servidor detenido")
        return {"success": True}

    @app.post("/backup", dependencies=[Depends(verify)])
    def backup():
        if mc.status == "running":
            mc.send_command("save-all")
            time.sleep(2)
        path = mc.backup_world()
        _sync_after_change()
        _log_act("backup", Path(path).name)
        return {"success": True, "backup_path": path}

    @app.post("/sync", dependencies=[Depends(verify)])
    def sync():
        _sync_after_change()
        return {"success": True}

    @app.get("/properties", dependencies=[Depends(verify)])
    def get_props():
        return {"properties": _read_props(mc.server_name)}

    @app.post("/properties", dependencies=[Depends(verify)])
    def set_props(req: PropsReq):
        ok = _write_props(mc.server_name, req.properties)
        _sync_after_change()
        _log_act("settings", "Propiedades actualizadas")
        return {"success": ok, "properties": _read_props(mc.server_name)}

    @app.get("/files", dependencies=[Depends(verify)])
    def list_files(path: str = Query("/")):
        target = _safe_path(path)
        if not target.exists():
            raise HTTPException(404, "Not found")
        if target.is_file():
            raise HTTPException(400, "Not a directory")
        files = []
        for item in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            stat = item.stat()
            files.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": _fmt_size(stat.st_size) if item.is_file() else "",
                    "size_bytes": stat.st_size if item.is_file() else 0,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                }
            )
        return {"files": files, "path": path}

    @app.get("/files/download", dependencies=[Depends(verify)])
    def download_file(path: str = Query(...)):
        target = _safe_path(path)
        if not target.exists():
            raise HTTPException(404, "File not found")
        if target.is_dir():
            buffer = BytesIO()
            with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
                for child in target.rglob("*"):
                    if child.is_file():
                        zf.write(child, child.relative_to(target.parent))
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{target.name}.zip"'},
            )
        return FileResponse(
            str(target),
            filename=target.name,
            media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        )

    @app.post("/files/rename", dependencies=[Depends(verify)])
    def rename_file(req: RenameReq):
        target = _safe_path(req.path)
        if not target.exists():
            raise HTTPException(404, "File not found")
        if not _rename_allowed(target.name, req.new_name, req.allow_extension_change):
            raise HTTPException(400, "Extension changes require advanced mode")
        new_path = target.parent / req.new_name
        if new_path.exists():
            raise HTTPException(409, "Already exists")
        target.rename(new_path)
        _sync_after_change()
        _log_act("files", f"Renombrado: {target.name} -> {req.new_name}")
        return {"success": True, "new_path": str(new_path.relative_to(_server_root()))}

    @app.delete("/files", dependencies=[Depends(verify)])
    def delete_file(path: str = Query(...)):
        target = _safe_path(path)
        if not target.exists():
            raise HTTPException(404, "Not found")
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            target.unlink()
        _sync_after_change()
        _log_act("files", f"Eliminado: {Path(path).name}")
        return {"success": True}

    @app.get("/backups", dependencies=[Depends(verify)])
    def list_backups():
        return {"backups": _list_backups()}

    @app.get("/backups/download", dependencies=[Depends(verify)])
    def download_backup(path: str = Query(...)):
        target = _safe_path(path, _backup_root())
        if not target.exists():
            raise HTTPException(404, "Backup not found")
        if target.is_dir():
            buffer = BytesIO()
            with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
                for child in target.rglob("*"):
                    if child.is_file():
                        zf.write(child, child.relative_to(target.parent))
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{target.name}.zip"'},
            )
        return FileResponse(str(target), filename=target.name)

    @app.post("/backups/rename", dependencies=[Depends(verify)])
    def rename_backup(req: RenameReq):
        target = _safe_path(req.path, _backup_root())
        if not target.exists():
            raise HTTPException(404, "Backup not found")
        new_path = target.parent / req.new_name
        if new_path.exists():
            raise HTTPException(409, "Already exists")
        target.rename(new_path)
        _sync_after_change()
        _log_act("backup", f"Backup renombrado: {req.new_name}")
        return {"success": True}

    @app.delete("/backups", dependencies=[Depends(verify)])
    def delete_backup(path: str = Query(...)):
        target = _safe_path(path, _backup_root())
        if not target.exists():
            raise HTTPException(404, "Backup not found")
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            target.unlink()
        _sync_after_change()
        _log_act("backup", f"Backup eliminado: {Path(path).name}")
        return {"success": True}

    @app.get("/activity", dependencies=[Depends(verify)])
    def get_activity():
        return {"events": list(reversed(activity_log[-100:]))}

    @app.post("/startup", dependencies=[Depends(verify)])
    def set_startup(req: StartupReq):
        _save_startup_config(req)
        _sync_after_change()
        return {"success": True, "config": _read_colabconfig(mc.server_name)}

    @app.get("/settings/general", dependencies=[Depends(verify)])
    def get_general_settings():
        props = _read_props(mc.server_name)
        cfg = _read_global_config()
        icon_file = _server_root() / "server-icon.png"
        return {
            "language": cfg.get("language", "en"),
            "motd": props.get("motd", ""),
            "has_icon": icon_file.exists(),
        }

    @app.post("/settings/language", dependencies=[Depends(verify)])
    def set_language(req: LangReq):
        cfg = _read_global_config()
        cfg["language"] = req.language
        _write_global_config(cfg)
        cc = _read_colabconfig(mc.server_name)
        if cc:
            cc["language"] = req.language
            _write_colabconfig(cc, mc.server_name)
        _log_act("settings", f"Idioma actualizado: {req.language}")
        return {"success": True, "language": req.language}

    @app.get("/settings/icon", dependencies=[Depends(verify)])
    def get_icon():
        icon_file = _server_root() / "server-icon.png"
        if not icon_file.exists():
            raise HTTPException(404, "Icon not found")
        return FileResponse(str(icon_file), media_type="image/png", filename="server-icon.png")

    @app.post("/settings/icon", dependencies=[Depends(verify)])
    def set_icon(req: IconReq):
        raw = req.image_data
        if "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            content = base64.b64decode(raw)
        except Exception:
            raise HTTPException(400, "Invalid image data")
        icon_file = _server_root() / "server-icon.png"
        icon_file.write_bytes(content)
        _sync_after_change()
        _log_act("settings", "Icono del servidor actualizado")
        return {"success": True}

    @app.post("/internal/update-tunnel")
    def update_tunnel(req: RegReq):
        if req.token != api_token:
            raise HTTPException(401)
        Path("/tmp/mci_tunnel_url.txt").write_text(req.url, encoding="utf-8")
        if lightnode_url:
            try:
                http_req.get(f"{lightnode_url}?token={api_token}&url={req.url}&server={mc.server_name}", timeout=8)
            except Exception:
                pass
        return {"success": True}

    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket, token: str = Query("")):
        if token != api_token:
            await ws.close(code=4001)
            return
        await ws.accept()
        ws_clients.append(ws)
        last_idx = max(0, len(mc.log_buffer) - 100)
        for line in mc.log_buffer[last_idx:]:
            try:
                await ws.send_text(line)
            except Exception:
                break
        try:
            while True:
                message = await asyncio.wait_for(ws.receive_text(), timeout=35)
                if message == "ping":
                    await ws.send_text("pong")
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        finally:
            if ws in ws_clients:
                ws_clients.remove(ws)

    return app


def _fmt_size(size_bytes):
    value = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
