import asyncio
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger("mci_core")


def normalize_jvm_mem(mem: str = "10G") -> str:
    raw = (mem or "2G").strip().upper()
    match = re.fullmatch(r"(\d+)([MG])", raw)
    if not match:
        return "2G"
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "M":
        return f"{max(amount, 1024)}M"
    return f"{max(amount, 2)}G"


def build_jvm_flags(server_type: str, mem: str = "10G") -> str:
    mem = normalize_jvm_mem(mem)
    base = f"-Xms{mem} -Xmx{mem}"
    aikar = (
        f"{base} -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 "
        "-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch "
        "-XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M "
        "-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 "
        "-XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 "
        "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 "
        "-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 "
        "-Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true"
    )
    if server_type in ("paper", "purpur", "arclight", "folia"):
        return aikar
    if server_type == "velocity":
        return (
            f"{base} -XX:+UseG1GC -XX:G1HeapRegionSize=4M "
            "-XX:+UnlockExperimentalVMOptions -XX:+ParallelRefProcEnabled "
            "-XX:+AlwaysPreTouch -XX:MaxInlineLevel=15"
        )
    return f"{base} -XX:+UseG1GC -XX:+AlwaysPreTouch"


def _required_java(server_type: str, version: str) -> int:
    if server_type == "velocity":
        return 17
    if server_type == "neoforge":
        return 21
    if server_type == "mohist":
        try:
            minor = int(version.split(".")[1])
            if minor <= 12:
                return 8
            if minor <= 16:
                return 11
            return 17
        except Exception:
            return 17
    try:
        parts = [int(part) for part in version.split(".")]
        major = parts[0] if parts else 1
        minor = parts[1] if len(parts) > 1 else 0
        patch = parts[2] if len(parts) > 2 else 0
    except Exception:
        return 21
    if major == 1:
        if minor >= 21 or (minor == 20 and patch >= 5):
            return 21
        if minor >= 17:
            return 17
        if minor >= 13:
            return 11
        return 8
    if major >= 26:
        return 25
    return 21


class MinecraftServer:
    def __init__(
        self,
        server_name,
        drive_path,
        local_path,
        server_type,
        server_version,
        jvm_mem="10G",
        custom_jvm_args=None,
        sync_interval=300,
    ):
        self.server_name = server_name
        self.drive_path = Path(drive_path)
        self.local_path = Path(local_path)
        self.server_type = server_type
        self.server_version = server_version
        self.jvm_mem = normalize_jvm_mem(jvm_mem)
        self.jvm_args = custom_jvm_args or build_jvm_flags(server_type, self.jvm_mem)
        self.sync_interval = sync_interval
        self.process: Optional[subprocess.Popen] = None
        self.status = "stopped"
        self.started_at: Optional[float] = None
        self._running = False
        self._log_callbacks: List[Callable] = []
        self._async_queue: Optional[asyncio.Queue] = None
        self.log_buffer: List[str] = []
        self.MAX_BUFFER = 2000
        self._last_line = ""
        self._last_tps: Optional[float] = None
        self._last_tps_source = "unknown"
        self._last_ping_ms: Optional[float] = None

    def set_async_queue(self, q: asyncio.Queue):
        self._async_queue = q

    def add_log_callback(self, cb):
        self._log_callbacks.append(cb)

    def _emit(self, line: str):
        ts = datetime.now().strftime("%H:%M:%S")
        stamped = f"[{ts}] {line}"
        self.log_buffer.append(stamped)
        if len(self.log_buffer) > self.MAX_BUFFER:
            self.log_buffer = self.log_buffer[-self.MAX_BUFFER :]
        for cb in self._log_callbacks:
            try:
                cb(stamped)
            except Exception:
                pass
        if self._async_queue is not None:
            try:
                self._async_queue.put_nowait(stamped)
            except asyncio.QueueFull:
                pass
        self._update_runtime_metrics(line)

    def _update_runtime_metrics(self, line: str):
        tps_match = re.search(r"TPS(?: from last [^:]+)?:\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)
        if tps_match:
            self._last_tps = round(float(tps_match.group(1)), 2)
            self._last_tps_source = "log"
            return
        if "Can't keep up!" in line:
            lag_match = re.search(r"Running (\d+)ms", line)
            if lag_match:
                delay_ms = max(int(lag_match.group(1)), 50)
                estimate = max(1.0, min(20.0, round(20.0 * 50.0 / delay_ms, 2)))
                self._last_tps = estimate
                self._last_tps_source = "lag-estimate"

    def sync_from_drive(self):
        src = str(self.drive_path / self.server_name) + "/"
        dst = str(self.local_path) + "/"
        self.local_path.mkdir(parents=True, exist_ok=True)
        self._emit("[MCI] Descargando datos del servidor...")
        rc = os.system(f'rsync -a --update --exclude="logs/" --exclude="session.lock" "{src}" "{dst}" 2>/dev/null')
        if rc != 0:
            src_path = self.drive_path / self.server_name
            if src_path.exists():
                shutil.copytree(str(src_path), str(self.local_path), dirs_exist_ok=True)
        self._emit("[MCI] Datos del servidor cargados.")

    def sync_to_drive(self):
        src = str(self.local_path) + "/"
        dst = str(self.drive_path / self.server_name) + "/"
        (self.drive_path / self.server_name).mkdir(parents=True, exist_ok=True)
        self._emit("[MCI] Guardando en Google Drive...")
        rc = os.system(f'rsync -a --update --exclude="logs/debug.log" "{src}" "{dst}" 2>/dev/null')
        if rc != 0:
            shutil.copytree(str(self.local_path), str(self.drive_path / self.server_name), dirs_exist_ok=True)
        self._emit("[MCI] Guardado en Drive.")

    def _sync_loop(self):
        while self._running:
            time.sleep(self.sync_interval)
            if self._running and self.status == "running":
                try:
                    self.sync_to_drive()
                except Exception as exc:
                    self._emit(f"[MCI] Error al guardar: {exc}")

    def backup_world(self) -> str:
        backup_base = self.drive_path / "backup" / "world"
        backup_base.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = backup_base / f"{self.server_name}_world_{ts}"
        backup_path.mkdir()
        for world_name in ["world", "world_nether", "world_the_end", "worlds"]:
            src = self.local_path / world_name
            if src.exists():
                shutil.copytree(str(src), str(backup_path / world_name))
        self._emit(f"[MCI] Copia de seguridad guardada: {backup_path.name}")
        return str(backup_path)

    def install_java(self):
        java_version = _required_java(self.server_type, self.server_version)
        self._emit(f"[MCI] Preparando Java {java_version}...")
        rc = os.system(
            f"java -version 2>&1 | grep -q '\"{java_version}\\.\\|\"{java_version}\"' || "
            f"(sudo apt-get update -qq > /dev/null 2>&1 && "
            f"sudo apt-get install -y -qq openjdk-{java_version}-jdk-headless > /dev/null 2>&1 && "
            f"sudo update-alternatives --install /usr/bin/java java "
            f"/usr/lib/jvm/java-{java_version}-openjdk-amd64/bin/java 1 > /dev/null 2>&1)"
        )
        if rc == 0:
            self._emit(f"[MCI] Java {java_version} listo.")
        else:
            self._emit(f"[MCI] Java {java_version} instalado (puede requerir verificacion).")

    def _jar_name(self) -> str:
        special = {
            "bedrock": "bedrock_server",
            "crucible": "Crucible-1.7.10-5.4.jar",
            "ketting": "kettinglauncher-1.5.1-sources.jar",
            "cardboard": "fabric-installer.jar",
            "magma": "magma-installer.jar",
            "custom": "server.jar",
        }
        return special.get(self.server_type, "server.jar")

    def _build_command(self) -> str:
        run_sh = self.local_path / "run.sh"
        if run_sh.exists() and self.server_type not in ("arclight",):
            cmd = run_sh.read_text(encoding="utf-8", errors="ignore")
            if "java" in cmd:
                cmd = cmd[cmd.find("java") :]
                cmd = cmd.replace("@user_jvm_args.txt", self.jvm_args).replace('"$@"', 'nogui "$@"')
                return cmd.strip()
        if self.server_type == "bedrock":
            return "LD_LIBRARY_PATH=. ./bedrock_server"
        return f"java -server {self.jvm_args} -jar {self._jar_name()} nogui"

    def _read_logs(self):
        if not self.process:
            return
        try:
            for raw in iter(self.process.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line == self._last_line:
                    continue
                self._last_line = line
                self._emit(line)
                if "Done" in line and ("help" in line or "For help" in line):
                    self.status = "running"
                    self.started_at = time.time()
                    self._emit("[MCI] Servidor en linea.")
        except Exception as exc:
            self._emit(f"[MCI] Error de log: {exc}")
        self.process.stdout.close()

    def _watchdog(self):
        if self.process:
            self.process.wait()
        if self._running:
            self._emit("[MCI] El servidor se ha detenido.")
            self.status = "stopped"
            self._running = False
            self.started_at = None

    def start(self) -> bool:
        if self.status in ("running", "starting"):
            return False
        self.status = "starting"
        self._running = True
        self.started_at = None
        lock = self.local_path / "world" / "session.lock"
        if lock.exists():
            lock.unlink()
        (self.local_path / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        if self.server_type != "bedrock":
            self.install_java()
        cmd = self._build_command()
        self._emit("[MCI] Iniciando servidor...")
        try:
            self.process = subprocess.Popen(
                cmd,
                shell=True,
                cwd=str(self.local_path),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
        except Exception as exc:
            self.status = "stopped"
            self._emit(f"[MCI] Error al iniciar: {exc}")
            return False
        threading.Thread(target=self._read_logs, daemon=True).start()
        threading.Thread(target=self._sync_loop, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        return True

    def stop(self):
        if self.status == "stopped":
            return
        self.status = "stopping"
        self._running = False
        self.send_command("stop")
        if self.process:
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.status = "stopped"
        self.started_at = None
        self.sync_to_drive()

    def send_command(self, command: str) -> bool:
        if self.process and self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.write(f"{command}\n".encode())
                self.process.stdin.flush()
                return True
            except Exception:
                return False
        return False

    def _server_properties(self) -> dict:
        props = {}
        prop_path = self.drive_path / self.server_name / "server.properties"
        if not prop_path.exists():
            return props
        for line in prop_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                props[key.strip()] = value.strip()
        return props

    def get_status(self) -> dict:
        props = self._server_properties()
        max_players = int(props.get("max-players", "20") or "20")
        server_port = int(props.get("server-port", "25565") or "25565")
        players = 0
        motd = props.get("motd", "")
        latency = None

        if self.status == "running":
            try:
                from mcstatus import JavaServer

                probe = JavaServer("127.0.0.1", server_port).status()
                players = probe.players.online
                motd = probe.description if probe.description else motd
                latency = round(probe.latency, 1)
                self._last_ping_ms = latency
            except Exception:
                pass

        cpu_usage = None
        memory_used = None
        memory_max = None
        try:
            import psutil

            cpu_usage = psutil.cpu_percent(interval=0.05)
            if self.process and self.process.poll() is None:
                proc = psutil.Process(self.process.pid)
                memory_used = round(proc.memory_info().rss / (1024 ** 3), 2)
                memory_max = self.jvm_mem.replace("G", "")
        except Exception:
            pass

        has_icon = (self.local_path / "server-icon.png").exists() or (self.drive_path / self.server_name / "server-icon.png").exists()
        return {
            "status": self.status,
            "players_online": players,
            "motd": motd,
            "latency_ms": latency if latency is not None else self._last_ping_ms,
            "tps": self._last_tps,
            "tps_source": self._last_tps_source,
            "server_type": self.server_type,
            "version": self.server_version,
            "server_name": self.server_name,
            "max_players": max_players,
            "memory_used": f"{memory_used:.2f} GB" if memory_used is not None else None,
            "memory_max": memory_max,
            "cpu_usage": cpu_usage,
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "server_port": server_port,
            "has_icon": has_icon,
        }
