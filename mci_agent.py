import json, logging, os, re, secrets, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path
from typing import Optional

LIGHTNODE_URL  = "https://prozoon700.x10.bz/MCI/lightnode.php"
PANEL_URL      = "https://prozoon700.x10.bz/MCI/index.html"
DRIVE_PATH     = "/content/drive/MyDrive/minecraft"
LOCAL_BASE     = "/content/mci_local"
TOKEN_FILE     = f"{DRIVE_PATH}/.mci_token"
TOOLS_DIR      = "/.mci_tools"
CONFIG_FILE    = f"{DRIVE_PATH}/.mci_config.json"
LOG_DIR        = f"{DRIVE_PATH}/logs"
API_PORT       = 8000
JVM_MEM        = "10G"
_LANG          = "en"

# ── Structured logger ────────────────────────────────────────────────────────
_logger: Optional[logging.Logger] = None

def _setup_logging() -> logging.Logger:
    global _logger
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_file = Path(LOG_DIR) / f"mci_{datetime.now().strftime('%Y-%m-%d')}.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    # File handler — full detail, always appended
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Stream handler — INFO+ to stderr (shows in Colab cell output)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("[MCI] %(levelname)s %(message)s"))

    logger = logging.getLogger("mci")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False

    _logger = logger
    logger.info(f"Log file: {log_file}")
    return logger

def log(msg: str, level: str = "info"):
    """Public log helper used by agent code."""
    if _logger is None:
        print(f"[MCI] {msg}", file=sys.stderr)
        return
    getattr(_logger, level.lower(), _logger.info)(msg)

# Keep _raw as alias for backward compatibility (called from notebook_cell.py)
def _raw(msg: str):
    log(msg, "info")

# ── Agent config ─────────────────────────────────────────────────────────────
def mount_drive():
    if Path("/content/drive/MyDrive").exists(): return
    from google.colab import drive
    drive.mount("/content/drive")

def load_agent_config():
    global JVM_MEM, API_PORT, _LANG
    p = Path(CONFIG_FILE)
    if not p.exists(): return {}
    try:
        cfg = json.loads(p.read_text())
        JVM_MEM  = cfg.get("jvm_mem", JVM_MEM)
        API_PORT = cfg.get("api_port", API_PORT)
        _LANG    = cfg.get("lang", "en")
        return cfg
    except Exception as e:
        log(f"Could not load config: {e}", "warning")
        return {}

def get_or_create_token() -> str:
    p = Path(TOKEN_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        token = p.read_text().strip()
        if len(token) >= 32:
            log(f"Loaded existing token: {token[:4]}…{token[-4:]}")
            return token
    token = secrets.token_hex(16)
    p.write_text(token)
    log(f"Generated new token: {token[:4]}…{token[-4:]}")
    return token

def load_server_config():
    """
    Returns (name, sc_dict, cc_dict) for the selected server.
    Returns (None, sc_dict, None) when no servers exist yet (first-run mode).
    """
    sc_path = Path(f"{DRIVE_PATH}/server_list.txt")
    if not sc_path.exists():
        log("No server_list.txt found — first-run mode (no servers configured)", "warning")
        empty = {"server_list": [], "server_in_use": ""}
        sc_path.write_text(json.dumps(empty, indent=2))
        return None, empty, None

    try:
        sc = json.loads(sc_path.read_text())
    except Exception as e:
        log(f"Corrupt server_list.txt: {e}", "error")
        sc = {"server_list": [], "server_in_use": ""}

    servers = [s for s in sc.get("server_list", [])
               if Path(f"{DRIVE_PATH}/{s}/colabconfig.txt").exists()]

    if not servers:
        log("server_list.txt exists but no valid servers found — first-run mode", "warning")
        return None, sc, None

    name = sc.get("server_in_use", "") or servers[0]
    if name not in servers:
        name = servers[0]
        sc["server_in_use"] = name
        sc_path.write_text(json.dumps(sc, indent=2))

    cc_path = Path(f"{DRIVE_PATH}/{name}/colabconfig.txt")
    try:
        cc = json.loads(cc_path.read_text())
    except Exception as e:
        log(f"Corrupt colabconfig.txt for '{name}': {e}", "error")
        return None, sc, None

    log(f"Loaded server config: {name} ({cc.get('server_type','?')} {cc.get('server_version','?')})")
    return name, sc, cc

# ── Port / process helpers ────────────────────────────────────────────────────
def _free_port(port: int):
    log(f"Freeing port {port}")
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'connections']):
            try:
                for conn in proc.connections(kind='inet'):
                    if conn.laddr.port == port:
                        proc.kill(); time.sleep(0.5); break
            except: pass
    except ImportError:
        pass
    os.system(f"fuser -k {port}/tcp 2>/dev/null || true")
    time.sleep(1)

# ── Cloudflared helper ────────────────────────────────────────────────────────
def _get_cloudflared() -> str:
    Path(TOOLS_DIR).mkdir(parents=True, exist_ok=True)
    cf = f"{TOOLS_DIR}/cloudflared"
    if not Path(cf).exists():
        log("Downloading cloudflared…")
        rc = os.system(
            f"wget -q -O {cf} "
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
            "2>/dev/null"
        )
        if rc == 0:
            os.chmod(cf, 0o755)
            log("cloudflared downloaded OK")
        else:
            log("cloudflared download failed", "error")
    return cf

# ── HTTP tunnel (API exposure) ────────────────────────────────────────────────
def start_http_tunnel(token: str, port: int) -> Optional[str]:
    cf = _get_cloudflared()
    log_file = "/tmp/mci_cf_http.log"
    log(f"Starting HTTP tunnel on port {port}…")
    try:
        subprocess.Popen(
            [cf, "tunnel", "--url", f"http://localhost:{port}"],
            stdout=open(log_file, "w"), stderr=subprocess.STDOUT
        )
    except FileNotFoundError:
        log("cloudflared binary not found — HTTP tunnel unavailable", "error")
        return None
    except Exception as e:
        log(f"HTTP tunnel start failed: {e}", "error")
        return None

    for attempt in range(45):
        time.sleep(2)
        try:
            text = Path(log_file).read_text()
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
            if m:
                url = m.group(0)
                Path("/tmp/mci_tunnel_url.txt").write_text(url)
                log(f"HTTP tunnel ready: {url}")
                register_lightnode(token, url, "")
                return url
        except Exception:
            pass
    log("HTTP tunnel timeout — no URL obtained after 90s", "warning")
    return None

def register_lightnode(token: str, tunnel_url: str, server_name: str):
    try:
        import requests as req
        url = f"{LIGHTNODE_URL}?token={token}&url={tunnel_url}"
        if server_name:
            url += f"&server={server_name}"
        r = req.get(url, timeout=10)
        log(f"Lightnode registered: HTTP {r.status_code}")
    except Exception as e:
        log(f"Lightnode registration failed: {e}", "warning")

# ── TCP tunnel (player connections) ──────────────────────────────────────────
# Tunnel status exposed to the panel via API
_tunnel_status = {"service": "none", "status": "pending", "address": None, "error": None}

def start_tcp_tunnel(tunnel_service: str, serverconfig: dict, local_path: str) -> Optional[str]:
    global _tunnel_status
    _tunnel_status = {"service": tunnel_service, "status": "starting", "address": None, "error": None}
    log(f"Starting TCP tunnel: {tunnel_service}")

    try:
        result = _start_tcp_tunnel_inner(tunnel_service, serverconfig, local_path)
        if result:
            _tunnel_status["status"] = "ok"
            _tunnel_status["address"] = result
            log(f"TCP tunnel ready: {result}")
        else:
            _tunnel_status["status"] = "unavailable"
            log(f"TCP tunnel '{tunnel_service}' returned no address", "warning")
        return result
    except FileNotFoundError as e:
        msg = f"Binary not found for tunnel '{tunnel_service}': {e.filename}"
        log(msg, "warning")
        _tunnel_status["status"] = "missing_binary"
        _tunnel_status["error"] = msg
        return None
    except Exception as e:
        msg = f"TCP tunnel error ({tunnel_service}): {e}"
        log(msg, "warning")
        _tunnel_status["status"] = "error"
        _tunnel_status["error"] = msg
        return None

def _start_tcp_tunnel_inner(tunnel_service: str, serverconfig: dict, local_path: str) -> Optional[str]:
    if tunnel_service == "argo":
        cf = _get_cloudflared()
        log_path = "/tmp/mci_cf_tcp.log"
        subprocess.Popen(
            [cf, "tunnel", "--url", "tcp://127.0.0.1:25565"],
            stdout=open(log_path, "w"), stderr=subprocess.STDOUT
        )
        for _ in range(30):
            time.sleep(2)
            try:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", Path(log_path).read_text())
                if m:
                    return m.group(0).replace("https://", "")
            except: pass
        return None

    if tunnel_service == "playit":
        sk = serverconfig.get("playit_proxy", {}).get("secretkey", "")
        if sk:
            try:
                import toml
                os.makedirs("/etc/playit", exist_ok=True)
                toml.dump({"secret_key": sk}, open("/etc/playit/playit.toml", "w"))
            except Exception as e:
                log(f"PlayIt config write failed: {e}", "warning")
        subprocess.Popen(
            ["playit", "-s", "start"],
            stdout=open("/tmp/mci_playit.log", "w"), stderr=subprocess.STDOUT
        )
        time.sleep(15)
        return "PlayIt dashboard"

    if tunnel_service == "ngrok":
        tok = serverconfig.get("ngrok_proxy", {}).get("authtoken", "")
        region = serverconfig.get("ngrok_proxy", {}).get("region", "us")
        if tok:
            os.system(f"ngrok authtoken {tok} 2>/dev/null")
        from pyngrok import conf, ngrok as pyngrok
        conf.get_default().region = region
        url = pyngrok.connect(25565, "tcp")
        return str(url).split('"')[1::2][0].replace("tcp://", "")

    if tunnel_service == "zrok":
        tok = serverconfig.get("zrok_proxy", {}).get("authtoken", "")
        if tok:
            os.system(f"zrok enable {tok} 2>/dev/null")
        subprocess.Popen(
            ["zrok", "share", "public", "tcp://localhost:25565"],
            stdout=open("/tmp/mci_zrok.log", "w"), stderr=subprocess.STDOUT
        )
        time.sleep(10)
        try:
            m = re.search(r"[\w-]+\.share\.zrok\.io:\d+", Path("/tmp/mci_zrok.log").read_text())
            return m.group(0) if m else None
        except: return None

    if tunnel_service == "localtonet":
        tok = serverconfig.get("localtonet_proxy", {}).get("authtoken", "")
        if tok:
            subprocess.Popen(
                ["localtonet", "tcp", "--port", "25565", "--authtoken", tok],
                stdout=open("/tmp/mci_localtonet.log", "w"), stderr=subprocess.STDOUT
            )
            time.sleep(10)
        return None

    log(f"Unknown tunnel service: {tunnel_service}", "warning")
    return None

# ── API server ────────────────────────────────────────────────────────────────
def start_api_server(app) -> threading.Thread:
    import uvicorn

    class _UvicornFilter(logging.Filter):
        """Suppress uvicorn access logs from polluting MCI log output."""
        def filter(self, record):
            return False

    # Quiet uvicorn
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.addFilter(_UvicornFilter())

    def _run():
        uvicorn.run(app, host="0.0.0.0", port=API_PORT, log_level="warning", access_log=False)

    t = threading.Thread(target=_run, daemon=True, name="mci-api")
    t.start()
    time.sleep(2)
    log(f"API server started on port {API_PORT}")
    return t

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    logger = _setup_logging()
    log("=" * 60)
    log(f"MCI Agent starting — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    load_agent_config()
    log(f"Config loaded: JVM_MEM={JVM_MEM}, API_PORT={API_PORT}, LANG={_LANG}")

    mount_drive()
    Path(DRIVE_PATH).mkdir(parents=True, exist_ok=True)
    _free_port(API_PORT)

    token = get_or_create_token()
    server_name, serverconfig, colabconfig = load_server_config()

    # ── First-run / no-server mode ────────────────────────────────────────
    if server_name is None:
        log("No servers configured — starting in panel-only mode")
        log("The web panel will guide the user through server creation.")

        # Need a minimal stub so create_app doesn't crash
        from mci_core import MinecraftServer
        _stub = MinecraftServer(
            server_name="_none_", drive_path=DRIVE_PATH,
            local_path=f"{LOCAL_BASE}/_none_",
            server_type="vanilla", server_version="",
            jvm_mem=JVM_MEM
        )
        from mci_api import create_app
        app = create_app(_stub, api_token=token, drive_path=DRIVE_PATH,
                         lightnode_url=LIGHTNODE_URL, panel_url=PANEL_URL)
        start_api_server(app)

        http_url = start_http_tunnel(token, API_PORT) or "(unavailable)"
        log(f"Panel-only mode ready. Token: {token}")
        log(f"Panel URL: {PANEL_URL}?token={token}")
        if http_url != "(unavailable)":
            register_lightnode(token, http_url, "")

        # Block until the agent is interrupted (user will create a server from panel)
        log("Waiting for server creation via panel… (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(10)
                # Re-check if a server has been created
                sc_path = Path(f"{DRIVE_PATH}/server_list.txt")
                if sc_path.exists():
                    sc = json.loads(sc_path.read_text())
                    if sc.get("server_list"):
                        log("Server detected in Drive — please re-run the cell to start it.")
                        break
        except KeyboardInterrupt:
            log("Agent stopped by user.")
        return

    # ── Normal server mode ────────────────────────────────────────────────
    local_path = Path(f"{LOCAL_BASE}/{server_name}")
    jvm_mem    = colabconfig.get("jvm_mem", JVM_MEM)
    log(f"Server: {server_name} | type={colabconfig.get('server_type')} "
        f"version={colabconfig.get('server_version')} mem={jvm_mem}")

    from mci_core import MinecraftServer
    mc = MinecraftServer(
        server_name=server_name, drive_path=DRIVE_PATH,
        local_path=str(local_path),
        server_type=colabconfig["server_type"],
        server_version=colabconfig["server_version"],
        jvm_mem=jvm_mem,
        custom_jvm_args=colabconfig.get("custom_jvm_args") or None,
        sync_interval=int(colabconfig.get("sync_interval", 300))
    )

    # Bridge MC log output → MCI logger
    def _mc_log(line: str):
        _logger.debug(f"[MC] {line}") if _logger else None
        # Also forward to file log as INFO so it's visible in the log file
        if _logger:
            _logger.getChild("mc").info(line)

    mc.add_log_callback(_mc_log)

    log("Syncing server data from Drive…")
    mc.sync_from_drive()
    log("Sync complete. Starting API server…")

    from mci_api import create_app
    app = create_app(mc, api_token=token, drive_path=DRIVE_PATH,
                     lightnode_url=LIGHTNODE_URL, panel_url=PANEL_URL,
                     tunnel_status_ref=_tunnel_status)
    start_api_server(app)

    log("Starting HTTP tunnel…")
    http_url = start_http_tunnel(token, API_PORT) or "(unavailable)"
    if http_url != "(unavailable)":
        register_lightnode(token, http_url, server_name)

    log("Starting TCP tunnel…")
    tcp_addr = start_tcp_tunnel(colabconfig.get("tunnel_service", "argo"), serverconfig, str(local_path))

    log(f"HTTP API tunnel : {http_url}")
    log(f"TCP game address: {tcp_addr or 'unavailable (' + _tunnel_status.get('status','?') + ')'}")
    log(f"Panel URL       : {PANEL_URL}?token={token}")
    log(f"Token           : {token}")

    log("Starting Minecraft server…")
    mc.start()

    try:
        while mc.status != "stopped":
            time.sleep(5)
    except KeyboardInterrupt:
        log("Interrupted — stopping server…")
        mc.stop()

    log("Syncing final state to Drive…")
    mc.sync_to_drive()
    log("Agent finished.")
    if _logger:
        for h in _logger.handlers:
            h.close()


if __name__ == "__main__":
    main()
