"""
mci_api.py — MineColab Improved v2.1
FastAPI control server — full feature set:
  /status  /command  /logs  /servers  /properties  /files  /backups
  /activity  /startup  /ws/logs  + offline queue flush
"""

import asyncio, json, logging, os, shutil, subprocess, time
from pathlib import Path
from typing import Optional, List

import requests as http_req
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

log = logging.getLogger("mci_api")
security = HTTPBearer(auto_error=False)

# ── Models ───────────────────────────────────────

class CmdReq(BaseModel):
    command: str

class PropsReq(BaseModel):
    properties: dict

class StartupReq(BaseModel):
    jvm_mem: Optional[str] = "10G"
    flag_preset: Optional[str] = "auto"
    custom_args: Optional[str] = ""
    tunnel_service: Optional[str] = "argo"
    sync_interval: Optional[int] = 300

class SelectReq(BaseModel):
    server: str

class RegReq(BaseModel):
    token: str
    url: str


class FileRenameReq(BaseModel):
    path: str
    new_name: str
    allow_extension_change: bool = False

# ── Factory ──────────────────────────────────────

def create_app(
    mc,             # MinecraftServer
    api_token: str,
    drive_path: str,
    lightnode_url: str = "",
    panel_url: str = "",
) -> FastAPI:

    app = FastAPI(title="MCI API", version="2.1.0", docs_url="/docs")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    log_queue: asyncio.Queue = asyncio.Queue(maxsize=8000)
    mc.set_async_queue(log_queue)
    ws_clients: list = []
    _t0 = time.time()
    activity_log: list = []
    drive = Path(drive_path)
    log_events: list = []
    log_seq = 0

    def _log_activity(typ: str, desc: str):
        activity_log.append({"type":typ,"description":desc,"ts":int(time.time())})
        if len(activity_log) > 500: activity_log.pop(0)

    def _append_log_event(line: str):
        nonlocal log_seq
        clean = str(line).rstrip("\n")
        if not clean:
            return None
        if log_events and log_events[-1]["line"] == clean:
            return None
        log_seq += 1
        event = {"seq": log_seq, "line": clean, "ts": time.time()}
        log_events.append(event)
        if len(log_events) > 4000:
            del log_events[:-4000]
        return event

    def verify(creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
        if not creds or creds.credentials != api_token:
            raise HTTPException(401, "Invalid token")
        return creds.credentials

    # ── Background tasks ─────────────────────────

    async def _broadcaster():
        while True:
            line = await log_queue.get()
            event = _append_log_event(line)
            if not event:
                continue
            dead = []
            for ws in list(ws_clients):
                try: await ws.send_json(event)
                except: dead.append(ws)
            for ws in dead:
                if ws in ws_clients: ws_clients.remove(ws)

    async def _flush_queue():
        """On startup, fetch pending offline changes from lightnode and apply."""
        await asyncio.sleep(5)
        if not lightnode_url or not api_token: return
        try:
            r = http_req.get(f"{lightnode_url}?action=flush&token={api_token}", timeout=10)
            d = r.json()
            changes = d.get("changes", [])
            for ch in changes:
                if ch["action"] == "/properties":
                    await _apply_properties(ch["data"])
                elif ch["action"] == "/startup":
                    await _apply_startup(ch["data"])
                log.info("Applied queued change: %s", ch["action"])
            if changes:
                mc._emit(f"[MCI] Applied {len(changes)} queued offline changes.")
        except Exception as e:
            log.warning("Queue flush failed: %s", e)

    async def _apply_properties(props: dict):
        sp = drive / mc.server_name / "server.properties"
        if not sp.exists(): return
        lines = sp.read_text().splitlines()
        out = []
        updated = set()
        for line in lines:
            if "=" in line and not line.startswith("#"):
                k = line.split("=",1)[0].strip()
                if k in props: out.append(f"{k}={props[k]}"); updated.add(k); continue
            out.append(line)
        for k,v in props.items():
            if k not in updated: out.append(f"{k}={v}")
        sp.write_text("\n".join(out))

    async def _apply_startup(cfg: dict):
        cc = drive / mc.server_name / "colabconfig.txt"
        if cc.exists():
            d = json.loads(cc.read_text())
            if "tunnel_service" in cfg: d["tunnel_service"] = cfg["tunnel_service"]
            cc.write_text(json.dumps(d, indent=2))

    @app.on_event("startup")
    async def _start():
        for line in mc.log_buffer[-200:]:
            _append_log_event(line)
        asyncio.create_task(_broadcaster())
        asyncio.create_task(_flush_queue())

    # ── Utility ───────────────────────────────────

    def _serverconfig():
        p = drive / "server_list.txt"
        if p.exists():
            return json.loads(p.read_text())
        return {}

    def _resolve_server_path(path: str) -> Path:
        base = (drive / mc.server_name).resolve()
        target = (base / path.lstrip("/")).resolve()
        if not str(target).startswith(str(base)):
            raise HTTPException(403, "Path outside server directory")
        return target

    def _display_path(path: Path) -> str:
        base = (drive / mc.server_name).resolve()
        rel = path.resolve().relative_to(base).as_posix()
        return "/" if rel == "." else f"/{rel}"

    def _backup_roots() -> list[Path]:
        candidates = [
            drive / "backup",
            drive / "backup" / "world",
            drive / mc.server_name / "backup",
        ]
        seen = set()
        roots = []
        for candidate in candidates:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists():
                roots.append(candidate)
        return roots

    def _read_props(server_name: str) -> dict:
        sp = drive / server_name / "server.properties"
        if not sp.exists(): return {}
        props = {}
        for line in sp.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=",1); props[k.strip()] = v.strip()
        return props

    def _write_props(server_name: str, props: dict):
        sp = drive / server_name / "server.properties"
        if not sp.exists(): return False
        lines = sp.read_text().splitlines()
        out = []
        updated = set()
        for line in lines:
            if "=" in line and not line.strip().startswith("#"):
                k = line.split("=",1)[0].strip()
                if k in props: out.append(f"{k}={props[k]}"); updated.add(k); continue
            out.append(line)
        for k,v in props.items():
            if k not in updated: out.append(f"{k}={v}")
        sp.write_text("\n".join(out))
        return True

    # ── Endpoints ─────────────────────────────────

    @app.get("/")
    def root(): return {"service":"MCI API","version":"2.1.0","uptime":round(time.time()-_t0)}

    @app.get("/health")
    def health(): return {"ok":True}

    @app.get("/status", dependencies=[Depends(verify)])
    def status():
        d = mc.get_status()
        d["api_uptime"] = round(time.time()-_t0)
        # Append cpu/disk hints
        try:
            cpu = open("/proc/loadavg").read().split()[0]
            d["cpu_usage"] = cpu
        except: pass
        try:
            stat = os.statvfs(str(drive))
            used_gb = round((stat.f_blocks - stat.f_bfree)*stat.f_frsize/1e9,1)
            d["disk_used"] = f"{used_gb} GB"
        except: pass
        return d

    # ── Servers list ──────────────────────────────
    @app.get("/servers", dependencies=[Depends(verify)])
    def list_servers():
        sc = _serverconfig()
        servers = []
        for name in sc.get("server_list", []):
            sp = drive / name
            if not sp.exists(): continue
            cc_path = sp / "colabconfig.txt"
            cc = {}
            if cc_path.exists():
                try: cc = json.loads(cc_path.read_text())
                except: pass
            s_status = mc.status if mc.server_name == name else "offline"
            servers.append({
                "name": name,
                "status": s_status,
                "server_type": cc.get("server_type",""),
                "version": cc.get("server_version",""),
                "players": mc.get_status().get("players_online",0) if mc.server_name==name else 0,
                "max_players": int(_read_props(name).get("max-players","20") or "20"),
                "memory_used": "—",
                "memory_max": "10",
                "uptime": "—",
            })
        current = sc.get("server_in_use","")
        return {"servers": servers, "current": current}

    @app.post("/servers/select", dependencies=[Depends(verify)])
    def select_server(req: SelectReq):
        sc = _serverconfig()
        if req.server not in sc.get("server_list",[]):
            raise HTTPException(404,"Server not found")
        sc["server_in_use"] = req.server
        (drive/"server_list.txt").write_text(json.dumps(sc, indent=2))
        return {"success":True,"server":req.server}

    # ── Command ───────────────────────────────────
    @app.post("/command", dependencies=[Depends(verify)])
    def command(req: CmdReq):
        if mc.status not in ("running","starting"):
            raise HTTPException(503,"Server not running")
        ok = mc.send_command(req.command)
        _log_activity("command", req.command)
        return {"success":ok,"command":req.command}

    # ── Logs ─────────────────────────────────────
    @app.get("/logs", dependencies=[Depends(verify)])
    def get_logs(lines: int = Query(200, ge=1, le=2000)):
        return {"logs": log_events[-lines:]}

    # ── Start / Stop / Backup / Sync ─────────────
    @app.post("/start", dependencies=[Depends(verify)])
    def start():
        if mc.status in ("running","starting"): return {"success":False,"message":"Already running"}
        ok = mc.start(); _log_activity("start","Server started")
        return {"success":ok}

    @app.post("/stop", dependencies=[Depends(verify)])
    def stop():
        mc.stop(); _log_activity("stop","Server stopped")
        return {"success":True}

    @app.post("/backup", dependencies=[Depends(verify)])
    def backup():
        try:
            path = mc.backup_world()
            try:
                mc.sync_to_drive()
            except Exception as sync_error:
                log.warning("Backup sync failed: %s", sync_error)
            _log_activity("backup", f"Backup: {Path(path).name}")
            return {"success":True,"backup_path":path}
        except Exception as e: raise HTTPException(500,str(e))

    @app.post("/sync", dependencies=[Depends(verify)])
    def sync():
        try: mc.sync_to_drive(); return {"success":True}
        except Exception as e: raise HTTPException(500,str(e))

    # ── Server properties ─────────────────────────
    @app.get("/properties", dependencies=[Depends(verify)])
    def get_props():
        return {"properties": _read_props(mc.server_name)}

    @app.post("/properties", dependencies=[Depends(verify)])
    def set_props(req: PropsReq):
        ok = _write_props(mc.server_name, req.properties)
        _log_activity("settings","Server properties updated")
        return {"success":ok}

    # ── Files ────────────────────────────────────
    @app.get("/files", dependencies=[Depends(verify)])
    def list_files(path: str = Query("/")):
        target = _resolve_server_path(path)
        if not target.exists(): raise HTTPException(404,"Path not found")
        if not target.is_dir(): raise HTTPException(400, "Path is not a directory")
        files = []
        for item in sorted(target.iterdir(), key=lambda x:(x.is_file(), x.name)):
            stat = item.stat()
            files.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "path": _display_path(item),
                "size": _fmt_size(stat.st_size) if item.is_file() else "",
                "bytes": stat.st_size if item.is_file() else 0,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                "extension": item.suffix,
            })
        return {"files": files, "path": path}

    @app.get("/files/download", dependencies=[Depends(verify)])
    def download_file(path: str = Query("/")):
        target = _resolve_server_path(path)
        if not target.exists():
            raise HTTPException(404, "Path not found")
        if not target.is_file():
            raise HTTPException(400, "Only files can be downloaded")
        return FileResponse(path=target, filename=target.name, media_type="application/octet-stream")

    @app.post("/files/rename", dependencies=[Depends(verify)])
    def rename_file(req: FileRenameReq):
        target = _resolve_server_path(req.path)
        if not target.exists():
            raise HTTPException(404, "Path not found")
        new_name = Path(req.new_name).name.strip()
        if not new_name:
            raise HTTPException(400, "New name is required")
        if new_name in (".", ".."):
            raise HTTPException(400, "Invalid file name")
        if target.is_file() and not req.allow_extension_change and Path(new_name).suffix != target.suffix:
            raise HTTPException(400, "Extension changes require advanced mode")
        renamed = target.with_name(new_name)
        if renamed.exists():
            raise HTTPException(409, "A file with that name already exists")
        target.rename(renamed)
        _log_activity("files", f"Renamed {_display_path(target)} to {new_name}")
        return {"success": True, "path": _display_path(renamed), "name": renamed.name}

    @app.delete("/files", dependencies=[Depends(verify)])
    def delete_file(path: str = Query("/")):
        target = _resolve_server_path(path)
        if not target.exists():
            raise HTTPException(404, "Path not found")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        _log_activity("files", f"Deleted {_display_path(target)}")
        return {"success": True}

    @app.get("/backups", dependencies=[Depends(verify)])
    def list_backups():
        items = []
        for root in _backup_roots():
            for d_item in root.iterdir():
                if not d_item.exists():
                    continue
                if d_item.is_dir():
                    sz = sum(f.stat().st_size for f in d_item.rglob("*") if f.is_file())
                else:
                    sz = d_item.stat().st_size
                items.append({
                    "name": d_item.name,
                    "size": _fmt_size(sz),
                    "date": time.strftime("%b %d %Y %H:%M", time.localtime(d_item.stat().st_mtime)),
                    "path": str(d_item),
                    "location": root.name,
                })
        items.sort(key=lambda item: Path(item["path"]).stat().st_mtime if Path(item["path"]).exists() else 0, reverse=True)
        return {"backups": items[:20]}

    # ── Activity ─────────────────────────────────
    @app.get("/activity", dependencies=[Depends(verify)])
    def get_activity(): return {"events": list(reversed(activity_log[-100:]))}

    # ── Startup config ───────────────────────────
    @app.post("/startup", dependencies=[Depends(verify)])
    def set_startup(req: StartupReq):
        cc_path = drive / mc.server_name / "colabconfig.txt"
        if cc_path.exists():
            try: cc = json.loads(cc_path.read_text())
            except: cc = {}
            cc["tunnel_service"] = req.tunnel_service
            cc["jvm_mem"] = req.jvm_mem
            cc["flag_preset"] = req.flag_preset
            cc["custom_jvm_args"] = req.custom_args
            cc["sync_interval"] = req.sync_interval
            cc_path.write_text(json.dumps(cc, indent=2))
        return {"success":True}

    # ── Internal: re-register tunnel ────────────
    @app.post("/internal/update-tunnel")
    def update_tunnel(req: RegReq):
        if req.token != api_token: raise HTTPException(401)
        Path("/tmp/mci_tunnel_url.txt").write_text(req.url)
        if lightnode_url:
            try:
                http_req.get(
                    f"{lightnode_url}?token={api_token}&url={req.url}&server={mc.server_name}",
                    timeout=8
                )
            except: pass
        return {"success":True,"url":req.url}

    # ── WebSocket ────────────────────────────────
    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket, token: str = Query("")):
        if token != api_token:
            await ws.close(code=4001, reason="Invalid token"); return
        await ws.accept()
        ws_clients.append(ws)
        for event in log_events[-100:]:
            try: await ws.send_json(event)
            except: break
        try:
            while True:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=35)
                if msg == "ping": await ws.send_text("pong")
        except (WebSocketDisconnect, asyncio.TimeoutError): pass
        finally:
            if ws in ws_clients: ws_clients.remove(ws)

    return app

def _fmt_size(b: int) -> str:
    for u in ["B","KB","MB","GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"
