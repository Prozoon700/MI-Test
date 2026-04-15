"""
mci_agent.py — MineColab Improved v2.1
Orchestrator: mounts Drive, generates token, kills busy ports,
starts all services, registers URL with lightnode via GET request.
"""

import json, logging, os, re, subprocess, sys, threading, time, uuid
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("mci_agent")

# ─────────────────────────────────────────────────────────────────
#  CONFIGURATION  (set once, persisted in Drive)
# ─────────────────────────────────────────────────────────────────
DRIVE_PATH     = "/content/drive/MyDrive/minecraft"
LOCAL_BASE     = "/content/mci_local"
TOKEN_FILE     = f"{DRIVE_PATH}/.mci_token"
TOOLS_DIR      = f"{DRIVE_PATH}/.mci_tools"
CONFIG_FILE    = f"{DRIVE_PATH}/.mci_config.json"   # persists agent settings

# Overridable via config file (populated by the notebook cell on first run)
LIGHTNODE_URL  = ""
PANEL_BASE_URL = ""
API_PORT       = 8000
JVM_MEM        = "10G"

# ─────────────────────────────────────────────────────────────────

def _box(lines, title=""):
    w = max(len(l) for l in lines) + 2
    b = "─"*(w+2)
    r = [f"╔{b}╗"]
    if title: r += [f"║  {title:<{w}}║", f"║{'─'*(w+2)}║"]
    r += [f"║  {l:<{w}}║" for l in lines]
    r.append(f"╚{b}╝")
    return "\n".join(r)

def _step(m): print(f"\033[1;96m[MCI]\033[0m {m}")
def _ok(m):   print(f"\033[1;92m[MCI ✓]\033[0m {m}")
def _warn(m): print(f"\033[1;93m[MCI ⚠]\033[0m {m}")
def _err(m):  print(f"\033[1;91m[MCI ✗]\033[0m {m}")

# ─────────────────────────────────────────────────────────────────
#  STEP 0 — Kill any process using API_PORT
# ─────────────────────────────────────────────────────────────────

def free_port(port: int):
    """Forcefully free a TCP port before binding."""
    _step(f"Freeing port {port}…")
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'connections']):
            try:
                for conn in proc.connections(kind='inet'):
                    if conn.laddr.port == port:
                        proc.kill()
                        _ok(f"Killed PID {proc.pid} (was using port {port})")
                        time.sleep(0.5)
                        break
            except Exception:
                pass
    except ImportError:
        # Fallback: use fuser
        os.system(f"fuser -k {port}/tcp 2>/dev/null")
    # Double-check with ss/lsof
    os.system(f"lsof -ti tcp:{port} | xargs -r kill -9 2>/dev/null || true")
    time.sleep(1)
    _ok(f"Port {port} freed.")

# ─────────────────────────────────────────────────────────────────
#  STEP 1 — Mount Drive
# ─────────────────────────────────────────────────────────────────

def mount_drive():
    if Path("/content/drive/MyDrive").exists():
        _ok("Drive already mounted."); return
    _step("Mounting Google Drive…")
    try:
        from google.colab import drive; drive.mount("/content/drive")
        _ok("Drive mounted.")
    except Exception as e:
        _err(f"Drive mount failed: {e}"); sys.exit(1)

# ─────────────────────────────────────────────────────────────────
#  STEP 2 — Load/save persistent agent config from Drive
# ─────────────────────────────────────────────────────────────────

def load_agent_config() -> dict:
    global LIGHTNODE_URL, PANEL_BASE_URL, JVM_MEM, API_PORT
    p = Path(CONFIG_FILE)
    if p.exists():
        try:
            cfg = json.loads(p.read_text())
            LIGHTNODE_URL  = cfg.get("lightnode_url", LIGHTNODE_URL)
            PANEL_BASE_URL = cfg.get("panel_url", PANEL_BASE_URL)
            JVM_MEM        = cfg.get("jvm_mem", JVM_MEM)
            API_PORT       = cfg.get("api_port", API_PORT)
            return cfg
        except Exception: pass
    return {}

def save_agent_config(cfg: dict):
    Path(CONFIG_FILE).write_text(json.dumps(cfg, indent=2))

# ─────────────────────────────────────────────────────────────────
#  STEP 3 — Install Python deps
# ─────────────────────────────────────────────────────────────────

def install_deps():
    pkgs = ["fastapi", "uvicorn[standard]", "websockets", "mcstatus", "httpx", "psutil"]
    _step("Installing Python dependencies…")
    for p in pkgs: os.system(f"pip install -q {p}")
    _ok("Dependencies ready.")

# ─────────────────────────────────────────────────────────────────
#  STEP 4 — Token
# ─────────────────────────────────────────────────────────────────

def get_or_create_token() -> str:
    p = Path(TOKEN_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        token = p.read_text().strip()
        _ok(f"Loaded token: {token[:4]}****")
        return token
    token = uuid.uuid4().hex[:8]
    p.write_text(token)
    _ok(f"New token: {token[:4]}****  (saved to Drive)")
    return token

# ─────────────────────────────────────────────────────────────────
#  STEP 5 — Read server config
# ─────────────────────────────────────────────────────────────────

def load_server_config():
    sc_path = Path(f"{DRIVE_PATH}/server_list.txt")
    if not sc_path.exists():
        _err("server_list.txt not found. Please run the Setup cell first."); sys.exit(1)
    sc = json.loads(sc_path.read_text())
    name = sc.get("server_in_use","")
    if not name:
        _err("No server selected. Run the 'Choose server' cell first."); sys.exit(1)
    cc = json.loads((Path(f"{DRIVE_PATH}/{name}/colabconfig.txt")).read_text())
    _ok(f"Server: {name}  ({cc['server_type']} {cc['server_version']})")
    return name, sc, cc

# ─────────────────────────────────────────────────────────────────
#  STEP 6 — Cloudflared
# ─────────────────────────────────────────────────────────────────

def _get_cloudflared() -> str:
    Path(TOOLS_DIR).mkdir(parents=True, exist_ok=True)
    cf = f"{TOOLS_DIR}/cloudflared"
    if not Path(cf).exists():
        _step("Downloading cloudflared…")
        os.system(f"wget -q -O {cf} https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64")
        os.chmod(cf, 0o755)
    return cf

def start_http_tunnel(token: str, port: int = API_PORT) -> Optional[str]:
    cf = _get_cloudflared()
    log_file = "/tmp/mci_cf_http.log"
    _step(f"Starting HTTPS tunnel for API (port {port})…")
    subprocess.Popen([cf, "tunnel", "--url", f"http://localhost:{port}"],
                     stdout=open(log_file,"w"), stderr=subprocess.STDOUT)
    for _ in range(40):
        time.sleep(2)
        try:
            content = Path(log_file).read_text()
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", content)
            if m:
                url = m.group(0)
                Path("/tmp/mci_tunnel_url.txt").write_text(url)
                _ok(f"HTTP tunnel: {url}")
                return url
        except Exception: pass
    _warn("Could not detect cloudflared URL after 80s.")
    return None

def start_tcp_tunnel(tunnel_service: str, serverconfig: dict, local_path: str) -> Optional[str]:
    if tunnel_service == "argo":
        cf = _get_cloudflared()
        log = "/tmp/mci_cf_tcp.log"
        subprocess.Popen([cf,"tunnel","--url","tcp://127.0.0.1:25565"],
                         stdout=open(log,"w"), stderr=subprocess.STDOUT)
        for _ in range(30):
            time.sleep(2)
            try:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", Path(log).read_text())
                if m: addr = m.group(0).replace("https://",""); _ok(f"TCP tunnel: {addr}"); return addr
            except: pass
        return None

    if tunnel_service == "playit":
        sk = serverconfig.get("playit_proxy",{}).get("secretkey","")
        if sk:
            try:
                import toml; os.makedirs("/etc/playit",exist_ok=True)
                toml.dump({"secret_key":sk}, open("/etc/playit/playit.toml","w"))
            except ImportError: pass
        subprocess.Popen(["playit","-s","start"], stdout=open("/tmp/mci_playit.log","w"), stderr=subprocess.STDOUT)
        time.sleep(15); return "See PlayIt dashboard"

    if tunnel_service == "ngrok":
        token = serverconfig.get("ngrok_proxy",{}).get("authtoken","")
        region = serverconfig.get("ngrok_proxy",{}).get("region","us")
        if token: os.system(f"ngrok authtoken {token}")
        try:
            from pyngrok import conf, ngrok as pyngrok
            conf.get_default().region = region
            url = pyngrok.connect(25565,"tcp")
            addr = str(url).split('"')[1::2][0].replace("tcp://","")
            _ok(f"Ngrok TCP: {addr}"); return addr
        except ImportError: _warn("pyngrok not installed")
        return None

    _warn(f"TCP tunnel '{tunnel_service}' — start manually from notebook cells.")
    return None

# ─────────────────────────────────────────────────────────────────
#  STEP 7 — Register with lightnode (GET request)
# ─────────────────────────────────────────────────────────────────

def register_lightnode(token: str, tunnel_url: str, server_name: str = ""):
    if not LIGHTNODE_URL: return
    try:
        import requests as req
        url = f"{LIGHTNODE_URL}?token={token}&url={tunnel_url}&server={server_name}"
        r = req.get(url, timeout=10)
        _ok(f"Lightnode registered ({r.status_code})")
    except Exception as e:
        _warn(f"Lightnode registration failed: {e}")

# ─────────────────────────────────────────────────────────────────
#  STEP 8 — Start FastAPI
# ─────────────────────────────────────────────────────────────────

def start_api_server(app) -> threading.Thread:
    import uvicorn
    def _run(): uvicorn.run(app, host="0.0.0.0", port=API_PORT, log_level="warning", access_log=False)
    t = threading.Thread(target=_run, daemon=True, name="mci-api")
    t.start(); time.sleep(2)
    _ok(f"FastAPI server on :{API_PORT}")
    return t

# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    print("\033[1;95m" + "═"*58 + "\n  MineColab Improved v2.1  —  Agent\n" + "═"*58 + "\033[0m\n")

    load_agent_config()
    mount_drive()
    install_deps()
    free_port(API_PORT)

    token = get_or_create_token()
    server_name, serverconfig, colabconfig = load_server_config()
    server_type    = colabconfig["server_type"]
    server_version = colabconfig["server_version"]
    tunnel_service = colabconfig.get("tunnel_service","argo")

    # Override JVM mem from colabconfig if set
    jvm_mem = colabconfig.get("jvm_mem", JVM_MEM)
    local_path = Path(f"{LOCAL_BASE}/{server_name}")

    from mci_core import MinecraftServer
    mc = MinecraftServer(
        server_name=server_name, drive_path=DRIVE_PATH, local_path=str(local_path),
        server_type=server_type, server_version=server_version,
        jvm_mem=jvm_mem,
        custom_jvm_args=colabconfig.get("custom_jvm_args") or None,
        sync_interval=int(colabconfig.get("sync_interval",300)),
    )
    mc.add_log_callback(lambda line: print(f"\033[0;37m{line}\033[0m"))
    mc.sync_from_drive()

    from mci_api import create_app
    app = create_app(mc, api_token=token, drive_path=DRIVE_PATH,
                     lightnode_url=LIGHTNODE_URL, panel_url=PANEL_BASE_URL)
    start_api_server(app)

    http_url = start_http_tunnel(token, API_PORT) or "(unavailable)"
    if http_url != "(unavailable)":
        register_lightnode(token, http_url, server_name)
        # Also notify the running API so it can re-register on demand
        try:
            import requests as req
            req.post(f"http://localhost:{API_PORT}/internal/update-tunnel",
                     json={"token":token,"url":http_url}, timeout=5)
        except: pass

    tcp_addr = start_tcp_tunnel(tunnel_service, serverconfig, str(local_path))

    lines = [f"✅  Server :  {server_name}", f"🔑  Token  :  {token}", "",
             f"🌐  API URL:  {http_url}"]
    if tcp_addr: lines.append(f"🎮  MC Addr:  {tcp_addr}")
    if PANEL_BASE_URL: lines += ["", f"🖥  Panel  :  {PANEL_BASE_URL}?token={token}"]
    print("\n" + _box(lines, "MCI v2.1 — Control Info") + "\n")

    mc.start()

    try:
        while mc.status != "stopped": time.sleep(5)
    except KeyboardInterrupt:
        _step("Interrupt — shutting down…"); mc.stop()

    mc.sync_to_drive()
    _ok("Agent finished.")


if __name__ == "__main__":
    main()
