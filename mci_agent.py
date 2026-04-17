import json, logging, os, re, secrets, subprocess, sys, threading, time
from pathlib import Path
from typing import Optional

LIGHTNODE_URL  = "https://prozoon700.x10.bz/MCI/lightnode.php"
PANEL_URL      = "https://prozoon700.x10.bz/MCI/index.html"
DRIVE_PATH     = "/content/drive/MyDrive/minecraft"
LOCAL_BASE     = "/content/mci_local"
TOKEN_FILE     = f"{DRIVE_PATH}/.mci_token"
TOOLS_DIR      = f"{DRIVE_PATH}/.mci_tools"
CONFIG_FILE    = f"{DRIVE_PATH}/.mci-config.json"
LEGACY_CONFIG_FILE = f"{DRIVE_PATH}/.mci_config.json"
API_PORT       = 8000
JVM_MEM        = "10G"

_file_log = None

def _setup_file_logging():
    global _file_log
    log_dir = Path(DRIVE_PATH) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    log_file = log_dir / f"mci_{datetime.now().strftime('%Y-%m-%d')}.log"
    _file_log = open(log_file, "a", encoding="utf-8", buffering=1)
    return _file_log

def _raw(msg: str):
    if _file_log:
        from datetime import datetime
        _file_log.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        _file_log.flush()

def _free_port(port: int):
    _raw(f"Freeing port {port}")
    try:
        import psutil
        for proc in psutil.process_iter(['pid','connections']):
            try:
                for conn in proc.connections(kind='inet'):
                    if conn.laddr.port == port:
                        proc.kill(); time.sleep(0.5); break
            except: pass
    except ImportError: pass
    os.system(f"lsof -ti tcp:{port} | xargs -r kill -9 2>/dev/null || true")
    os.system(f"fuser -k {port}/tcp 2>/dev/null || true")
    time.sleep(1)

def mount_drive():
    if Path("/content/drive/MyDrive").exists(): return
    from google.colab import drive; drive.mount("/content/drive")

def load_agent_config():
    global JVM_MEM, API_PORT
    p = Path(CONFIG_FILE)
    if not p.exists() and Path(LEGACY_CONFIG_FILE).exists():
        p = Path(LEGACY_CONFIG_FILE)
    if not p.exists(): return {}
    try:
        cfg = json.loads(p.read_text())
        JVM_MEM   = cfg.get("jvm_mem", JVM_MEM)
        API_PORT  = cfg.get("api_port", API_PORT)
        return cfg
    except: return {}

def get_or_create_token() -> str:
    p = Path(TOKEN_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        token = p.read_text().strip()
        if len(token) >= 32: return token
    token = secrets.token_hex(16)  # 32 hex chars = 16 bytes entropy
    p.write_text(token)
    return token

def load_server_config():
    sc_path = Path(f"{DRIVE_PATH}/server_list.txt")
    if not sc_path.exists(): raise RuntimeError("server_list.txt no encontrado. Ejecuta la celda de configuración primero.")
    sc = json.loads(sc_path.read_text())
    name = sc.get("server_in_use","")
    if not name: raise RuntimeError("No hay servidor seleccionado. Usa la celda 'Elegir servidor'.")
    cc_path = Path(f"{DRIVE_PATH}/{name}/colabconfig.txt")
    if not cc_path.exists(): raise RuntimeError(f"Configuración no encontrada para '{name}'.")
    cc = json.loads(cc_path.read_text())
    return name, sc, cc

def _get_cloudflared() -> str:
    Path(TOOLS_DIR).mkdir(parents=True, exist_ok=True)
    cf = f"{TOOLS_DIR}/cloudflared"
    if not Path(cf).exists():
        os.system(f"wget -q -O {cf} https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 2>/dev/null")
        os.chmod(cf, 0o755)
    return cf

def start_http_tunnel(token: str, port: int) -> Optional[str]:
    cf = _get_cloudflared()
    log_file = "/tmp/mci_cf_http.log"
    subprocess.Popen([cf, "tunnel", "--url", f"http://localhost:{port}"],
                     stdout=open(log_file,"w"), stderr=subprocess.STDOUT)
    for _ in range(45):
        time.sleep(2)
        try:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", Path(log_file).read_text())
            if m:
                url = m.group(0)
                Path("/tmp/mci_tunnel_url.txt").write_text(url)
                _raw(f"HTTP tunnel: {url}")
                register_lightnode(token, url, "")
                return url
        except: pass
    return None

def register_lightnode(token: str, tunnel_url: str, server_name: str):
    try:
        import requests as req
        url = f"{LIGHTNODE_URL}?token={token}&url={tunnel_url}"
        if server_name: url += f"&server={server_name}"
        r = req.get(url, timeout=10)
        _raw(f"Lightnode registered: {r.status_code} {r.text[:100]}")
    except Exception as e:
        _raw(f"Lightnode error: {e}")

def start_tcp_tunnel(tunnel_service: str, serverconfig: dict, local_path: str) -> Optional[str]:
    if tunnel_service == "argo":
        cf = _get_cloudflared()
        log = "/tmp/mci_cf_tcp.log"
        subprocess.Popen([cf,"tunnel","--url","tcp://127.0.0.1:25565"],
                         stdout=open(log,"w"),stderr=subprocess.STDOUT)
        for _ in range(30):
            time.sleep(2)
            try:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", Path(log).read_text())
                if m: return m.group(0).replace("https://","")
            except: pass
        return None
    if tunnel_service == "playit":
        sk = serverconfig.get("playit_proxy",{}).get("secretkey","")
        if sk:
            try:
                import toml; os.makedirs("/etc/playit",exist_ok=True)
                toml.dump({"secret_key":sk},open("/etc/playit/playit.toml","w"))
            except: pass
        subprocess.Popen(["playit","-s","start"],stdout=open("/tmp/mci_playit.log","w"),stderr=subprocess.STDOUT)
        time.sleep(15); return "PlayIt dashboard"
    if tunnel_service == "ngrok":
        tok = serverconfig.get("ngrok_proxy",{}).get("authtoken","")
        region = serverconfig.get("ngrok_proxy",{}).get("region","us")
        if tok: os.system(f"ngrok authtoken {tok}")
        try:
            from pyngrok import conf, ngrok as pyngrok
            conf.get_default().region = region
            url = pyngrok.connect(25565,"tcp")
            return str(url).split('"')[1::2][0].replace("tcp://","")
        except: return None
    return None

def start_api_server(app) -> threading.Thread:
    import uvicorn
    def _run(): uvicorn.run(app, host="0.0.0.0", port=API_PORT, log_level="error", access_log=False)
    t = threading.Thread(target=_run, daemon=True, name="mci-api")
    t.start(); time.sleep(2); return t

def main():
    _setup_file_logging()
    _raw("MCI agent starting")
    load_agent_config()
    mount_drive()
    _free_port(API_PORT)

    token = get_or_create_token()
    _raw(f"Token: {token[:4]}…{token[-4:]}")

    server_name, serverconfig, colabconfig = load_server_config()
    _raw(f"Server: {server_name}")

    local_path = Path(f"{LOCAL_BASE}/{server_name}")
    jvm_mem = colabconfig.get("jvm_mem", JVM_MEM)

    from mci_core import MinecraftServer
    mc = MinecraftServer(server_name=server_name, drive_path=DRIVE_PATH, local_path=str(local_path),
        server_type=colabconfig["server_type"], server_version=colabconfig["server_version"],
        jvm_mem=jvm_mem, custom_jvm_args=colabconfig.get("custom_jvm_args") or None,
        sync_interval=int(colabconfig.get("sync_interval",300)))

    mc.add_log_callback(lambda line: _raw(line))
    mc.sync_from_drive()

    from mci_api import create_app
    app = create_app(mc, api_token=token, drive_path=DRIVE_PATH,
                     lightnode_url=LIGHTNODE_URL, panel_url=PANEL_URL)
    start_api_server(app)

    http_url = start_http_tunnel(token, API_PORT) or "(unavailable)"

    if http_url != "(unavailable)":
        register_lightnode(token, http_url, server_name)

    tcp_addr = start_tcp_tunnel(colabconfig.get("tunnel_service","argo"), serverconfig, str(local_path))

    _raw(f"HTTP tunnel: {http_url}")
    _raw(f"TCP addr: {tcp_addr}")
    _raw(f"Panel: {PANEL_URL}?token={token}")

    mc.start()

    try:
        while mc.status != "stopped": time.sleep(5)
    except KeyboardInterrupt:
        mc.stop()

    mc.sync_to_drive()
    _raw("Agent finished")
    if _file_log: _file_log.close()


if __name__ == "__main__":
    main()
