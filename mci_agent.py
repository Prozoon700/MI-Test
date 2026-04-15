"""
mci_agent.py — MineColab Improved v2
Master orchestrator: mounts Drive, generates token, starts all services,
launches tunnels, registers with the light node, and keeps everything alive.

Run from a Colab cell with:
    !python3 /content/mci_agent.py
or
    exec(open('/content/mci_agent.py').read())
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mci_agent")

# ─────────────────────────────────────────────
#  CONFIGURATION  — edit these before first run
# ─────────────────────────────────────────────

DRIVE_PATH = "/content/drive/MyDrive/minecraft"
LOCAL_BASE  = "/content/mci_local"
TOKEN_FILE  = f"{DRIVE_PATH}/.mci_token"
TOOLS_DIR   = f"{DRIVE_PATH}/.mci_tools"

# Optional: set to your lightnode URL, e.g. "https://mypanel.example.com"
# Leave empty ("") to skip lightnode registration.
LIGHTNODE_URL = ""

# Optional: base URL of your static panel, e.g. "https://mypanel.example.com"
# Leave empty ("") to skip the panel link.
PANEL_BASE_URL = ""

# Minecraft API port (do NOT change unless you know what you're doing)
API_PORT = 8000

# JVM memory allocation
JVM_MEM = "10G"

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def _box(lines: list[str], title: str = "") -> str:
    width = max(len(l) for l in lines) + 2
    border = "─" * (width + 2)
    out = [f"╔{border}╗"]
    if title:
        out.append(f"║  {title:<{width}}║")
        out.append(f"║{'─' * (width + 2)}║")
    for l in lines:
        out.append(f"║  {l:<{width}}║")
    out.append(f"╚{border}╝")
    return "\n".join(out)


def _step(msg: str):
    print(f"\033[1;96m[MCI]\033[0m {msg}")


def _ok(msg: str):
    print(f"\033[1;92m[MCI ✓]\033[0m {msg}")


def _warn(msg: str):
    print(f"\033[1;93m[MCI ⚠]\033[0m {msg}")


def _err(msg: str):
    print(f"\033[1;91m[MCI ✗]\033[0m {msg}")


# ─────────────────────────────────────────────
#  STEP 1: Mount Google Drive
# ─────────────────────────────────────────────

def mount_drive():
    if not Path("/content/drive/MyDrive").exists():
        _step("Mounting Google Drive…")
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            _ok("Drive mounted.")
        except Exception as e:
            _err(f"Could not mount Drive: {e}")
            sys.exit(1)
    else:
        _ok("Drive already mounted.")


# ─────────────────────────────────────────────
#  STEP 2: Install Python dependencies
# ─────────────────────────────────────────────

def install_deps():
    pkgs = [
        "fastapi",
        "uvicorn[standard]",
        "websockets",
        "mcstatus",
        "httpx",
    ]
    _step("Installing Python dependencies…")
    for pkg in pkgs:
        os.system(f"pip install -q {pkg}")
    _ok("Dependencies installed.")


# ─────────────────────────────────────────────
#  STEP 3: Token management
# ─────────────────────────────────────────────

def get_or_create_token() -> str:
    p = Path(TOKEN_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        token = p.read_text().strip()
        _ok(f"Loaded existing token: {token[:4]}****")
        return token
    token = uuid.uuid4().hex[:8]
    p.write_text(token)
    _ok(f"Generated new token: {token[:4]}****  (saved to Drive)")
    return token


# ─────────────────────────────────────────────
#  STEP 4: Read server config from Drive
# ─────────────────────────────────────────────

def load_server_config() -> tuple[str, dict, dict]:
    """Returns (server_name, serverconfig, colabconfig)."""
    sc_path = Path(f"{DRIVE_PATH}/server_list.txt")
    if not sc_path.exists():
        _err("server_list.txt not found. Please run the Setup cell first.")
        sys.exit(1)

    with open(sc_path) as f:
        serverconfig = json.load(f)

    server_name = serverconfig.get("server_in_use", "")
    if not server_name:
        _err("No server selected. Please run the 'Choose Server' cell first.")
        sys.exit(1)

    cc_path = Path(f"{DRIVE_PATH}/{server_name}/colabconfig.txt")
    if not cc_path.exists():
        _err(f"colabconfig.txt not found for '{server_name}'.")
        sys.exit(1)

    with open(cc_path) as f:
        colabconfig = json.load(f)

    _ok(f"Server: {server_name}  ({colabconfig['server_type']} {colabconfig['server_version']})")
    return server_name, serverconfig, colabconfig


# ─────────────────────────────────────────────
#  STEP 5: Cloudflared tunnels
# ─────────────────────────────────────────────

def _get_cloudflared() -> str:
    """Download cloudflared binary once; return path."""
    Path(TOOLS_DIR).mkdir(parents=True, exist_ok=True)
    cf = f"{TOOLS_DIR}/cloudflared"
    if not Path(cf).exists():
        _step("Downloading cloudflared…")
        os.system(
            f"wget -q -O {cf} "
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
        )
        os.chmod(cf, 0o755)
        _ok("cloudflared downloaded.")
    return cf


def start_http_tunnel(api_token: str, port: int = API_PORT) -> Optional[str]:
    """
    Start an ephemeral cloudflared HTTPS tunnel for the FastAPI control API.
    Returns the public HTTPS URL or None on failure.
    """
    cf = _get_cloudflared()
    log_file = "/tmp/mci_cf_http.log"

    _step(f"Starting HTTP tunnel for API (port {port})…")
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
    )

    for attempt in range(40):
        time.sleep(2)
        try:
            content = Path(log_file).read_text()
        except Exception:
            continue
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", content)
        if m:
            url = m.group(0)
            _ok(f"HTTP tunnel ready: {url}")
            # Persist for mci_api to pick up
            Path("/tmp/mci_tunnel_url.txt").write_text(url)
            return url

    _warn("Could not detect cloudflared URL after 80 s.")
    return None


def start_tcp_tunnel(tunnel_service: str, serverconfig: dict, local_path: str) -> Optional[str]:
    """
    Start the TCP tunnel for Minecraft players using the service configured
    during server creation.  Returns a human-readable address string or None.
    """
    if tunnel_service == "argo":
        _step("Starting TCP tunnel via cloudflared (argo)…")
        cf = _get_cloudflared()
        log = "/tmp/mci_cf_tcp.log"
        subprocess.Popen(
            [cf, "tunnel", "--url", "tcp://127.0.0.1:25565"],
            stdout=open(log, "w"),
            stderr=subprocess.STDOUT,
        )
        for _ in range(30):
            time.sleep(2)
            try:
                content = Path(log).read_text()
            except Exception:
                continue
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", content)
            if m:
                addr = m.group(0).replace("https://", "")
                _ok(f"TCP tunnel: {addr}")
                return addr
        return None

    if tunnel_service == "playit":
        _step("Starting PlayIt tunnel…")
        sk = serverconfig.get("playit_proxy", {}).get("secretkey", "")
        if sk:
            import toml
            os.makedirs("/etc/playit", exist_ok=True)
            toml.dump({"secret_key": sk}, open("/etc/playit/playit.toml", "w"))
        log = "/tmp/mci_playit.log"
        subprocess.Popen(
            ["playit", "-s", "start"],
            stdout=open(log, "w"),
            stderr=subprocess.STDOUT,
        )
        time.sleep(15)
        _ok("PlayIt agent started. Check dashboard for address.")
        return "See PlayIt dashboard"

    if tunnel_service == "ngrok":
        _step("Starting Ngrok TCP tunnel…")
        token  = serverconfig.get("ngrok_proxy", {}).get("authtoken", "")
        region = serverconfig.get("ngrok_proxy", {}).get("region", "us")
        if token:
            os.system(f"ngrok authtoken {token}")
        from pyngrok import conf, ngrok as pyngrok
        conf.get_default().region = region
        url = pyngrok.connect(25565, "tcp")
        addr = str(url).split('"')[1::2][0].replace("tcp://", "")
        _ok(f"Ngrok TCP: {addr}")
        return addr

    if tunnel_service == "zrok":
        _step("Starting Zrok TCP tunnel…")
        zrok = f"{local_path}/tunnel/zrok/zrok"
        log  = "/tmp/mci_zrok.log"
        subprocess.Popen(
            [zrok, "share", "private", "--backend-mode", "tcpTunnel",
             "127.0.0.1:25565", "--headless"],
            stdout=open(log, "w"),
            stderr=subprocess.STDOUT,
        )
        time.sleep(12)
        _ok("Zrok started. Run 'zrok access private <token>' to connect.")
        return "See Zrok logs (/tmp/mci_zrok.log)"

    _warn(f"TCP tunnel '{tunnel_service}' not auto-started by mci_agent. "
          "Start it manually from the notebook tunnel cells.")
    return None


# ─────────────────────────────────────────────
#  STEP 6: Lightnode registration
# ─────────────────────────────────────────────

def register_lightnode(token: str, tunnel_url: str):
    if not LIGHTNODE_URL:
        return
    import requests as req
    try:
        r = req.post(
            f"{LIGHTNODE_URL}/api/register",
            json={"token": token, "url": tunnel_url},
            timeout=10,
        )
        _ok(f"Lightnode registered ({r.status_code})")
    except Exception as e:
        _warn(f"Lightnode registration failed: {e}")


# ─────────────────────────────────────────────
#  STEP 7: Start FastAPI (uvicorn)
# ─────────────────────────────────────────────

def start_api_server(app) -> threading.Thread:
    import uvicorn

    def _run():
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=API_PORT,
            log_level="warning",
            access_log=False,
        )

    t = threading.Thread(target=_run, daemon=True, name="mci-api")
    t.start()
    time.sleep(2)
    _ok(f"FastAPI control server started on :{API_PORT}")
    return t


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print("\033[1;95m" + "═" * 55)
    print("  MineColab Improved v2  —  Agent Starting")
    print("═" * 55 + "\033[0m\n")

    # 1. Drive
    mount_drive()

    # 2. Dependencies
    install_deps()

    # 3. Token
    token = get_or_create_token()

    # 4. Config
    server_name, serverconfig, colabconfig = load_server_config()
    server_type    = colabconfig["server_type"]
    server_version = colabconfig["server_version"]
    tunnel_service = colabconfig.get("tunnel_service", "argo")

    local_path = Path(f"{LOCAL_BASE}/{server_name}")

    # 5. Minecraft core
    from mci_core import MinecraftServer
    mc = MinecraftServer(
        server_name=server_name,
        drive_path=DRIVE_PATH,
        local_path=str(local_path),
        server_type=server_type,
        server_version=server_version,
        jvm_mem=JVM_MEM,
    )

    # Attach console logger
    mc.add_log_callback(lambda line: print(f"\033[0;37m{line}\033[0m"))

    # 6. Sync from Drive → local
    mc.sync_from_drive()

    # 7. FastAPI app
    from mci_api import create_app
    app = create_app(
        minecraft_server=mc,
        api_token=token,
        lightnode_url=LIGHTNODE_URL,
        panel_base_url=PANEL_BASE_URL,
    )

    # 8. Start API
    start_api_server(app)

    # 9. HTTP tunnel (API)
    http_url = start_http_tunnel(token, API_PORT) or "(unavailable)"

    # 10. Register with lightnode
    if http_url != "(unavailable)":
        register_lightnode(token, http_url)

    # 11. TCP tunnel (players)
    tcp_addr = start_tcp_tunnel(tunnel_service, serverconfig, str(local_path))

    # 12. Welcome box
    lines = [
        f"✅  Server :  {server_name}",
        f"🔑  Token  :  {token}",
        f"",
        f"🌐  API URL:  {http_url}",
    ]
    if tcp_addr:
        lines.append(f"🎮  MC Addr:  {tcp_addr}")
    if PANEL_BASE_URL:
        lines.append(f"")
        lines.append(f"🖥  Panel  :  {PANEL_BASE_URL}?token={token}")
    print("\n" + _box(lines, "MCI v2 — Control Info") + "\n")

    # 13. Start Minecraft
    mc.start()

    # 14. Block until Minecraft exits
    try:
        while mc.status != "stopped":
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n")
        _step("Interrupt received — shutting down…")
        mc.stop()

    _step("Final Drive sync…")
    mc.sync_to_drive()
    _ok("MCI Agent finished.")


if __name__ == "__main__":
    main()
