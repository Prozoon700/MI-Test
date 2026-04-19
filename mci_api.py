import asyncio, json, logging, mimetypes, os, shutil, time
from pathlib import Path
from typing import Optional
import requests as http_req
from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse
from pydantic import BaseModel

log = logging.getLogger("mci_api")
security = HTTPBearer(auto_error=False)

class CmdReq(BaseModel): command: str
class PropsReq(BaseModel): properties: dict
class StartupReq(BaseModel):
    jvm_mem: Optional[str] = "10G"; flag_preset: Optional[str] = "auto"
    custom_args: Optional[str] = ""; tunnel_service: Optional[str] = "argo"
    sync_interval: Optional[int] = 300
class SelectReq(BaseModel): server: str
class RegReq(BaseModel): token: str; url: str
class RenameReq(BaseModel): path: str; new_name: str
class CreateServerReq(BaseModel):
    name: str; mc_version: str; server_type: str
    software_version: Optional[str] = "latest"
    tunnel_service: str = "argo"
    tunnel_token: Optional[str] = ""
    jvm_mem: Optional[str] = "10G"
class LangReq(BaseModel): lang: str


def create_app(mc, api_token: str, drive_path: str, lightnode_url: str = "", panel_url: str = "") -> FastAPI:
    app = FastAPI(title="MCI API", version="2.2.0", docs_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    log_queue: asyncio.Queue = asyncio.Queue(maxsize=8000)
    mc.set_async_queue(log_queue)
    ws_clients: list = []
    _t0 = time.time()
    activity_log: list = []
    drive = Path(drive_path)

    def verify(creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
        if not creds or creds.credentials != api_token: raise HTTPException(401, "Invalid token")
        return creds.credentials

    def _log_act(typ, desc): activity_log.append({"type":typ,"description":desc,"ts":int(time.time())}); activity_log[:] = activity_log[-500:]

    async def _broadcaster():
        while True:
            line = await log_queue.get()
            for ws in list(ws_clients):
                try: await ws.send_text(line)
                except: ws_clients.remove(ws) if ws in ws_clients else None

    async def _flush_queue():
        await asyncio.sleep(6)
        if not lightnode_url: return
        try:
            r = http_req.get(f"{lightnode_url}?action=flush&token={api_token}", timeout=10)
            for ch in r.json().get("changes",[]):
                if ch["action"] == "/properties": await _apply_props(ch["data"])
                elif ch["action"] == "/startup": await _apply_startup(ch["data"])
        except: pass

    async def _apply_props(props):
        sp = drive / _active_server() / "server.properties"
        if not sp.exists(): return
        lines = sp.read_text().splitlines(); out = []; done = set()
        for line in lines:
            if "=" in line and not line.startswith("#"):
                k = line.split("=",1)[0].strip()
                if k in props: out.append(f"{k}={props[k]}"); done.add(k); continue
            out.append(line)
        for k,v in props.items():
            if k not in done: out.append(f"{k}={v}")
        sp.write_text("\n".join(out))

    async def _apply_startup(cfg):
        cc = drive / _active_server() / "colabconfig.txt"
        if cc.exists():
            d = json.loads(cc.read_text())
            for k,v in cfg.items(): d[k] = v
            cc.write_text(json.dumps(d, indent=2))

    @app.on_event("startup")
    async def _start():
        asyncio.create_task(_broadcaster())
        asyncio.create_task(_flush_queue())

    def _active_server() -> str:
        """Returns the currently selected server name from server_list.txt.
        Falls back to mc.server_name (the running server) if not set."""
        try:
            sc_path = drive / "server_list.txt"
            if sc_path.exists():
                sc = json.loads(sc_path.read_text())
                name = sc.get("server_in_use", "")
                if name and (drive / name).exists():
                    return name
        except: pass
        return mc.server_name

    def _read_props(name):
        sp = drive / name / "server.properties"
        if not sp.exists(): return {}
        props = {}
        for line in sp.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k,v = line.split("=",1); props[k.strip()] = v.strip()
        return props

    def _write_props(name, props):
        sp = drive / name / "server.properties"
        if not sp.exists(): return False
        lines = sp.read_text().splitlines(); out = []; done = set()
        for line in lines:
            if "=" in line and not line.strip().startswith("#"):
                k = line.split("=",1)[0].strip()
                if k in props: out.append(f"{k}={props[k]}"); done.add(k); continue
            out.append(line)
        for k,v in props.items():
            if k not in done: out.append(f"{k}={v}")
        sp.write_text("\n".join(out)); return True

    def _safe_path(path_str: str):
        srv = _active_server()
        # Use local_path for the currently running server (files are up-to-date there)
        # Use drive path for other servers
        if srv == mc.server_name:
            base = mc.local_path
        else:
            base = drive / srv
        target = (base / path_str.lstrip("/")).resolve()
        if not str(target).startswith(str(base.resolve())): raise HTTPException(403, "Access denied")
        return target

    @app.get("/health")
    def health(): return {"ok": True}

    @app.get("/status", dependencies=[Depends(verify)])
    def status():
        d = mc.get_status(); d["api_uptime"] = round(time.time()-_t0); d["active_server"] = _active_server()
        try: d["cpu_usage"] = open("/proc/loadavg").read().split()[0]
        except: pass
        try:
            stat = os.statvfs(str(drive))
            d["disk_used"] = f"{round((stat.f_blocks-stat.f_bfree)*stat.f_frsize/1e9,1)} GB"
        except: pass
        return d

    @app.get("/servers", dependencies=[Depends(verify)])
    def list_servers():
        sc_path = drive / "server_list.txt"
        if not sc_path.exists(): return {"servers":[],"current":""}
        sc = json.loads(sc_path.read_text())
        servers = []
        for name in sc.get("server_list",[]):
            if not (drive/name).exists(): continue
            cc = {}
            try: cc = json.loads((drive/name/"colabconfig.txt").read_text())
            except: pass
            servers.append({"name":name,"status":mc.status if mc.server_name==name else "offline",
                "server_type":cc.get("server_type",""),"version":cc.get("server_version",""),
                "players":mc.get_status().get("players_online",0) if mc.server_name==name else 0,
                "max_players":int(_read_props(name).get("max-players","20") or "20"),
                "memory_used":"—","memory_max":"10","uptime":"—"})
        return {"servers":servers,"current":sc.get("server_in_use","")}

    @app.post("/servers/select", dependencies=[Depends(verify)])
    def select_server(req: SelectReq):
        sc_path = drive/"server_list.txt"
        try:
            sc = json.loads(sc_path.read_text())
        except:
            sc = {"server_list":[],"server_in_use":""}
        sc["server_in_use"] = req.server; sc_path.write_text(json.dumps(sc,indent=2))
        return {"success":True, "active":req.server, "running":mc.server_name}

    @app.post("/command", dependencies=[Depends(verify)])
    def command(req: CmdReq):
        if mc.status not in ("running","starting"): raise HTTPException(503,"Server not running")
        ok = mc.send_command(req.command); _log_act("command", req.command)
        return {"success":ok,"command":req.command}

    @app.get("/logs", dependencies=[Depends(verify)])
    def get_logs(lines: int = Query(200, ge=1, le=2000), since: int = Query(0)):
        buf = mc.log_buffer
        if since > 0 and since < len(buf): buf = buf[since:]
        else: buf = buf[-lines:]
        return {"logs":buf,"total":len(mc.log_buffer)}

    @app.post("/start", dependencies=[Depends(verify)])
    def start():
        if mc.status in ("running","starting"): return {"success":False,"message":"Ya en ejecución"}
        ok = mc.start(); _log_act("start","Servidor iniciado")
        return {"success":ok}

    @app.post("/stop", dependencies=[Depends(verify)])
    def stop():
        mc.stop(); _log_act("stop","Servidor detenido"); return {"success":True}

    @app.post("/backup", dependencies=[Depends(verify)])
    def backup():
        if mc.status == "running": mc.send_command("save-all"); time.sleep(3)
        path = mc.backup_world(); _log_act("backup",Path(path).name)
        # Ensure backup is also synced to Drive
        try:
            backup_src = Path(path)
            backup_dst = drive / "backup" / "world" / backup_src.name
            if not backup_dst.exists() and backup_src.exists():
                shutil.copytree(str(backup_src), str(backup_dst))
        except: pass
        return {"success":True,"backup_path":path}

    @app.post("/sync", dependencies=[Depends(verify)])
    def sync():
        mc.sync_to_drive(); return {"success":True}

    @app.get("/properties", dependencies=[Depends(verify)])
    def get_props(): return {"properties":_read_props(_active_server())}

    @app.post("/properties", dependencies=[Depends(verify)])
    def set_props(req: PropsReq):
        ok = _write_props(_active_server(), req.properties); _log_act("settings","Propiedades actualizadas")
        return {"success":ok}

    @app.get("/files", dependencies=[Depends(verify)])
    def list_files(path: str = Query("/")):
        target = _safe_path(path)
        if not target.exists(): raise HTTPException(404,"Not found")
        if target.is_file(): raise HTTPException(400,"Not a directory")
        files = []
        for item in sorted(target.iterdir(), key=lambda x:(x.is_file(), x.name)):
            stat = item.stat()
            files.append({"name":item.name,"type":"dir" if item.is_dir() else "file",
                "size":_fmt_size(stat.st_size) if item.is_file() else "",
                "size_bytes":stat.st_size if item.is_file() else 0,
                "modified":time.strftime("%Y-%m-%d %H:%M",time.localtime(stat.st_mtime))})
        return {"files":files,"path":path}

    @app.get("/files/download", dependencies=[Depends(verify)])
    def download_file(path: str = Query(...)):
        target = _safe_path(path)
        if not target.exists() or not target.is_file(): raise HTTPException(404,"File not found")
        return FileResponse(str(target), filename=target.name,
            media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    @app.post("/files/rename", dependencies=[Depends(verify)])
    def rename_file(req: RenameReq):
        target = _safe_path(req.path)
        if not target.exists(): raise HTTPException(404,"File not found")
        new_path = target.parent / req.new_name
        if new_path.exists(): raise HTTPException(409,"Already exists")
        target.rename(new_path)
        srv=_active_server(); base=mc.local_path if srv==mc.server_name else drive/srv
        return {"success":True,"new_path":str(new_path.relative_to(base))}

    @app.delete("/files", dependencies=[Depends(verify)])
    def delete_file(path: str = Query(...)):
        target = _safe_path(path)
        if not target.exists(): raise HTTPException(404,"Not found")
        if target.is_dir(): shutil.rmtree(str(target))
        else: target.unlink()
        return {"success":True}

    @app.get("/backups", dependencies=[Depends(verify)])
    def list_backups():
        bp = drive / "backup" / "world"
        if not bp.exists(): return {"backups": []}
        items = []
        for f in sorted(bp.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
            items.append({"name": f.name, "size": _fmt_size(f.stat().st_size),
                "date": time.strftime("%d %b %Y %H:%M", time.localtime(f.stat().st_mtime))})
            if len(items) >= 20: break
        return {"backups": items}

    @app.get("/backups/download", dependencies=[Depends(verify)])
    def download_backup(name: str = Query(...)):
        target = drive / "backup" / "world" / name
        if not target.exists() or not name.endswith(".zip"):
            raise HTTPException(404, "Backup not found")
        return FileResponse(str(target), filename=target.name, media_type="application/zip")

    @app.delete("/backups", dependencies=[Depends(verify)])
    def delete_backup(name: str = Query(...)):
        target = drive / "backup" / "world" / name
        if not target.exists(): raise HTTPException(404, "Not found")
        target.unlink()
        return {"success": True}

    @app.post("/backups/rename", dependencies=[Depends(verify)])
    def rename_backup(req: RenameReq):
        target = drive / "backup" / "world" / req.path
        if not target.exists(): raise HTTPException(404, "Not found")
        new_name = req.new_name if req.new_name.endswith(".zip") else req.new_name + ".zip"
        new_path = target.parent / new_name
        if new_path.exists(): raise HTTPException(409, "Already exists")
        target.rename(new_path)
        return {"success": True}

    @app.get("/activity", dependencies=[Depends(verify)])
    def get_activity(): return {"events":list(reversed(activity_log[-100:]))}

    @app.post("/startup", dependencies=[Depends(verify)])
    def set_startup(req: StartupReq):
        cc_path = drive / _active_server() / "colabconfig.txt"
        if cc_path.exists():
            try: cc = json.loads(cc_path.read_text())
            except: cc = {}
            cc.update({"tunnel_service":req.tunnel_service,"jvm_mem":req.jvm_mem,
                       "flag_preset":req.flag_preset,"custom_jvm_args":req.custom_args,
                       "sync_interval":req.sync_interval})
            cc_path.write_text(json.dumps(cc,indent=2))
        return {"success":True}

    @app.post("/internal/update-tunnel")
    def update_tunnel(req: RegReq):
        if req.token != api_token: raise HTTPException(401)
        Path("/tmp/mci_tunnel_url.txt").write_text(req.url)
        if lightnode_url:
            try: http_req.get(f"{lightnode_url}?token={api_token}&url={req.url}&server={mc.server_name}",timeout=8)
            except: pass
        return {"success":True}

    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket, token: str = Query("")):
        if token != api_token: await ws.close(code=4001); return
        await ws.accept()
        ws_clients.append(ws)
        last_idx = max(0, len(mc.log_buffer)-100)
        for line in mc.log_buffer[last_idx:]:
            try: await ws.send_text(line)
            except: break
        try:
            while True:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=35)
                if msg == "ping": await ws.send_text("pong")
        except (WebSocketDisconnect, asyncio.TimeoutError): pass
        finally:
            if ws in ws_clients: ws_clients.remove(ws)

    # ── Language config ────────────────────────────────────────────────────
    @app.post("/config/lang", dependencies=[Depends(verify)])
    def set_language(req: LangReq):
        cfg_path = drive / ".mci_config.json"
        try:
            cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            cfg["lang"] = req.lang
            cfg_path.write_text(json.dumps(cfg, indent=2))
        except: pass
        return {"success": True}

    @app.get("/config", dependencies=[Depends(verify)])
    def get_config():
        cfg_path = drive / ".mci_config.json"
        try:
            return json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        except: return {}

    # ── Versions ────────────────────────────────────────────────────────────
    @app.get("/versions/minecraft")
    def mc_versions():
        try:
            r = http_req.get("https://launchermeta.mojang.com/mc/game/version_manifest.json", timeout=10)
            versions = [v["id"] for v in r.json()["versions"] if v["type"] == "release"][:25]
            return {"versions": versions}
        except:
            return {"versions": ["1.21.4","1.21.3","1.21.1","1.20.6","1.20.4","1.20.1",
                                  "1.19.4","1.18.2","1.17.1","1.16.5","1.12.2","1.8.8"]}

    @app.get("/versions/software")
    def sw_versions(type: str = Query("paper"), mc: str = Query("1.21.4")):
        try:
            if type in ("paper", "purpur", "folia", "velocity", "waterfall"):
                project = "paper" if type == "paper" else type
                r = http_req.get(
                    f"https://api.papermc.io/v2/projects/{project}/versions/{mc}/builds",
                    timeout=10)
                builds = r.json().get("builds", [])
                versions = [{"id": str(b["build"]), "label": f"Build {b['build']}"} for b in reversed(builds[-10:])]
                return {"versions": [{"id": "latest", "label": "Latest"}] + versions}
            elif type == "fabric":
                r = http_req.get("https://meta.fabricmc.net/v2/versions/loader", timeout=10)
                loaders = [{"id": v["version"], "label": f"Loader {v['version']}"} for v in r.json()[:8]]
                return {"versions": [{"id": "latest", "label": "Latest"}] + loaders}
        except: pass
        return {"versions": [{"id": "latest", "label": "Latest"}]}

    # ── Server creation ─────────────────────────────────────────────────────
    async def _get_jar_url(server_type: str, mc_version: str, sw_version: str) -> str:
        import asyncio
        def _fetch():
            try:
                if server_type == "vanilla":
                    manifest = http_req.get(
                        "https://launchermeta.mojang.com/mc/game/version_manifest.json",
                        timeout=10).json()
                    ver = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
                    if not ver: return ""
                    return http_req.get(ver["url"], timeout=10).json()["downloads"]["server"]["url"]

                elif server_type in ("paper", "purpur", "folia", "waterfall", "velocity"):
                    project = server_type
                    if sw_version and sw_version != "latest":
                        build = sw_version
                    else:
                        builds = http_req.get(
                            f"https://api.papermc.io/v2/projects/{project}/versions/{mc_version}/builds",
                            timeout=10).json()
                        build = str(builds["builds"][-1]["build"])
                    data = http_req.get(
                        f"https://api.papermc.io/v2/projects/{project}/versions/{mc_version}/builds/{build}",
                        timeout=10).json()
                    jar = data["downloads"]["application"]["name"]
                    return f"https://api.papermc.io/v2/projects/{project}/versions/{mc_version}/builds/{build}/downloads/{jar}"

                elif server_type == "fabric":
                    loader_r = http_req.get("https://meta.fabricmc.net/v2/versions/loader", timeout=10).json()
                    loader = sw_version if sw_version and sw_version != "latest" else loader_r[0]["version"]
                    inst_r = http_req.get("https://meta.fabricmc.net/v2/versions/installer", timeout=10).json()
                    launcher = inst_r[0]["version"]
                    return f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader}/{launcher}/server/jar"

            except Exception as e:
                log.error(f"JAR URL error: {e}")
            return ""
        return await asyncio.to_thread(_fetch)

    @app.post("/servers/create", dependencies=[Depends(verify)])
    async def create_server(req: CreateServerReq):
        name = req.name.strip().replace(" ", "_")
        if not name or "/" in name or ".." in name:
            raise HTTPException(400, "Invalid server name")
        server_path = drive / name
        if server_path.exists():
            raise HTTPException(409, f"Server '{name}' already exists")
        server_path.mkdir(parents=True)
        try:
            # Save tunnel token if provided
            sc_path = drive / "server_list.txt"
            sc = json.loads(sc_path.read_text()) if sc_path.exists() else {"server_list": [], "server_in_use": ""}
            if req.tunnel_token and req.tunnel_service not in ("argo",):
                proxy_key = f"{req.tunnel_service}_proxy"
                sc.setdefault(proxy_key, {})["authtoken"] = req.tunnel_token
                if req.tunnel_service == "playit":
                    sc.setdefault(proxy_key, {})["secretkey"] = req.tunnel_token

            # Download JAR
            jar_url = await _get_jar_url(req.server_type, req.mc_version, req.software_version or "latest")
            if not jar_url:
                shutil.rmtree(str(server_path))
                raise HTTPException(400, f"Could not find JAR for {req.server_type} {req.mc_version}")

            def _dl():
                r = http_req.get(jar_url, stream=True, timeout=180)
                r.raise_for_status()
                with open(str(server_path / "server.jar"), "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
            await asyncio.to_thread(_dl)

            # colabconfig
            (server_path / "colabconfig.txt").write_text(json.dumps({
                "server_type": req.server_type, "server_version": req.mc_version,
                "software_version": req.software_version or "latest",
                "tunnel_service": req.tunnel_service, "jvm_mem": req.jvm_mem or "10G",
                "sync_interval": 300
            }, indent=2))
            # eula
            (server_path / "eula.txt").write_text("eula=true\n")

            # Update server_list.txt
            if name not in sc.get("server_list", []):
                sc.setdefault("server_list", []).append(name)
            if not sc.get("server_in_use"):
                sc["server_in_use"] = name
            sc_path.write_text(json.dumps(sc, indent=2))

            _log_act("create", f"Server '{name}' created ({req.server_type} {req.mc_version})")
            return {"success": True, "server": name}
        except HTTPException: raise
        except Exception as e:
            shutil.rmtree(str(server_path), ignore_errors=True)
            raise HTTPException(500, str(e))

    return app

def _fmt_size(b):
    for u in ["B","KB","MB","GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"