import os, re, shutil, subprocess, threading, time, logging, zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional
import asyncio

log = logging.getLogger("mci_core")

def build_jvm_flags(server_type: str, mem: str = "10G") -> str:
    base = f"-Xms512m -Xmx{mem}"
    aikar = (f"{base} -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 "
             "-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch "
             "-XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M "
             "-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 "
             "-XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 "
             "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 "
             "-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 "
             # Use all available threads on the Colab VM
             "-XX:ConcGCThreads=2 -XX:ParallelGCThreads=4 "
             "-Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true")
    if server_type in ("paper","purpur","arclight","folia"): return aikar
    if server_type == "velocity":
        return (f"{base} -XX:+UseG1GC -XX:G1HeapRegionSize=4M "
                "-XX:+UnlockExperimentalVMOptions -XX:+ParallelRefProcEnabled -XX:+AlwaysPreTouch -XX:MaxInlineLevel=15")
    return f"{base} -XX:+UseG1GC -XX:+AlwaysPreTouch"

def _required_java(server_type: str, version: str) -> int:
    if server_type == "velocity": return 17
    if server_type == "neoforge": return 21
    if server_type == "mohist":
        try:
            m = int(version.split(".")[1])
            if m <= 12: return 8
            if m <= 16: return 11
            return 17
        except: return 17
    try:
        parts = [int(x) for x in version.split(".")]
        major = parts[0] if parts else 1
        minor = parts[1] if len(parts) > 1 else 0
        patch = parts[2] if len(parts) > 2 else 0
    except:
        return 21
    if major == 1:
        if minor >= 21 or (minor == 20 and patch >= 5): return 21
        if minor >= 17: return 17
        if minor >= 13: return 11
        return 8
    if major >= 26: return 25
    return 21


class MinecraftServer:
    def __init__(self, server_name, drive_path, local_path, server_type, server_version,
                 jvm_mem="10G", custom_jvm_args=None, sync_interval=300):
        self.server_name = server_name
        self.drive_path = Path(drive_path)
        self.local_path = Path(local_path)
        self.server_type = server_type
        self.server_version = server_version
        self.jvm_args = custom_jvm_args or build_jvm_flags(server_type, jvm_mem)
        self.sync_interval = sync_interval
        self.process: Optional[subprocess.Popen] = None
        self.status = "stopped"
        self._running = False
        self._log_callbacks: List[Callable] = []
        self._async_queue: Optional[asyncio.Queue] = None
        self.log_buffer: List[str] = []
        self.MAX_BUFFER = 2000

    def set_async_queue(self, q: asyncio.Queue): self._async_queue = q

    def add_log_callback(self, cb): self._log_callbacks.append(cb)

    def _emit(self, line: str):
        ts = datetime.now().strftime("%H:%M:%S")
        stamped = f"[{ts}] {line}"
        self.log_buffer.append(stamped)
        if len(self.log_buffer) > self.MAX_BUFFER:
            self.log_buffer = self.log_buffer[-self.MAX_BUFFER:]
        # Call registered callbacks (→ file logger) but never print to stdout/stderr
        # so the Colab cell output stays clean.
        for cb in self._log_callbacks:
            try: cb(stamped)
            except: pass
        if self._async_queue is not None:
            try: self._async_queue.put_nowait(stamped)
            except asyncio.QueueFull: pass

    def sync_from_drive(self):
        src = str(self.drive_path / self.server_name) + "/"
        dst = str(self.local_path) + "/"
        self.local_path.mkdir(parents=True, exist_ok=True)
        self._emit("[MCI] Descargando datos del servidor…")
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
        self._emit("[MCI] Guardando en Google Drive…")
        rc = os.system(f'rsync -a --update --exclude="logs/debug.log" "{src}" "{dst}" 2>/dev/null')
        if rc != 0:
            shutil.copytree(str(self.local_path), str(self.drive_path / self.server_name), dirs_exist_ok=True)
        self._emit("[MCI] Guardado en Drive.")

    def _sync_loop(self):
        while self._running:
            time.sleep(self.sync_interval)
            if self._running and self.status == "running":
                try: self.sync_to_drive()
                except Exception as e: self._emit(f"[MCI] Error al guardar: {e}")

    def backup_world(self) -> str:
        backup_base = self.drive_path / "backup" / "world"
        backup_base.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        zip_path = backup_base / f"{self.server_name}_world_{ts}.zip"
        worlds = ["world", "world_nether", "world_the_end", "worlds"]
        backed = []
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for w in worlds:
                src = self.local_path / w
                if src.exists():
                    for fp in src.rglob("*"):
                        if fp.is_file():
                            zf.write(str(fp), str(fp.relative_to(self.local_path)))
                    backed.append(w)
        self._emit(f"[MCI] Backup guardado: {zip_path.name} ({', '.join(backed)})")
        return str(zip_path)

    def install_java(self):
        jver = _required_java(self.server_type, self.server_version)
        self._emit(f"[MCI] Preparando Java {jver}…")
        rc = os.system(
            f"java -version 2>&1 | grep -q '\"{ jver }\\.\\|\"{ jver }\"' || "
            f"(sudo apt-get update -qq > /dev/null 2>&1 && "
            f"sudo apt-get install -y -qq openjdk-{jver}-jdk-headless > /dev/null 2>&1 && "
            f"sudo update-alternatives --install /usr/bin/java java "
            f"/usr/lib/jvm/java-{jver}-openjdk-amd64/bin/java 1 > /dev/null 2>&1)"
        )
        if rc == 0:
            self._emit(f"[MCI] Java {jver} listo.")
        else:
            self._emit(f"[MCI] Java {jver} instalado (puede requerir verificación).")

    def _jar_name(self) -> str:
        special = {"bedrock":"bedrock_server","crucible":"Crucible-1.7.10-5.4.jar",
                   "ketting":"kettinglauncher-1.5.1-sources.jar","cardboard":"fabric-installer.jar",
                   "magma":"magma-installer.jar","custom":"server.jar"}
        return special.get(self.server_type, "server.jar")

    def _build_command(self) -> str:
        run_sh = self.local_path / "run.sh"
        if run_sh.exists() and self.server_type not in ("arclight","fabric"):
            cmd = run_sh.read_text()
            if "java" in cmd:
                cmd = cmd[cmd.find("java"):]
                cmd = cmd.replace("@user_jvm_args.txt", self.jvm_args).replace('"$@"', 'nogui "$@"')
                return cmd.strip()
        if self.server_type == "bedrock": return "LD_LIBRARY_PATH=. ./bedrock_server"
        # Fabric: downloaded jar IS the installer/launcher — run without nogui on first boot
        if self.server_type == "fabric":
            launch = self.local_path / "fabric-server-launch.jar"
            if launch.exists():
                return f"java {self.jvm_args} -jar fabric-server-launch.jar nogui"
            # First run: installer generates fabric-server-launch.jar
            return f"java -jar {self._jar_name()} server -mcversion {self.server_version} -downloadMinecraft"
        return f"java {self.jvm_args} -jar {self._jar_name()} nogui"

    def _read_logs(self):
        if not self.process: return
        try:
            for raw in iter(self.process.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                self._emit(line)
                if "Done" in line and ("help" in line or "For help" in line):
                    self.status = "running"
                    self._emit("[MCI] ✅ ¡Servidor en línea!")
        except Exception as e: self._emit(f"[MCI] Error de log: {e}")
        self.process.stdout.close()

    def _watchdog(self):
        if self.process:
            rc = self.process.wait()
            time.sleep(0.8)  # let _read_logs flush remaining output before emitting "stopped"
            if rc != 0 and self.status not in ("running", "stopping"):
                self._emit(f"[MCI] ERROR: Java exited with code {rc}. Check logs above for details.")
        if self._running:
            self._emit("[MCI] El servidor se ha detenido."); self.status = "stopped"; self._running = False

    def start(self) -> bool:
        if self.status in ("running","starting"): return False
        self.status = "starting"; self._running = True
        lock = self.local_path / "world" / "session.lock"
        if lock.exists(): lock.unlink()
        (self.local_path / "eula.txt").write_text("eula=true\n")
        # Verify JAR exists
        jar = self.local_path / self._jar_name()
        if self.server_type not in ("bedrock",) and not jar.exists():
            files = list(self.local_path.iterdir()) if self.local_path.exists() else []
            self._emit(f"[MCI] ERROR: JAR not found: {jar}")
            self._emit(f"[MCI] Files in {self.local_path}: {[f.name for f in files]}")
            self.status = "stopped"; return False
        if self.server_type != "bedrock": self.install_java()
        cmd = self._build_command()
        self._emit(f"[MCI] Iniciando servidor…")
        self._emit(f"[MCI] CMD: {cmd[:160]}")
        self._emit(f"[MCI] CWD: {self.local_path}")
        try:
            self.process = subprocess.Popen(cmd, shell=True, cwd=str(self.local_path),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0)
        except Exception as e:
            self.status = "stopped"; self._emit(f"[MCI] Error al iniciar: {e}"); return False
        threading.Thread(target=self._read_logs, daemon=True).start()
        threading.Thread(target=self._sync_loop, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        return True

    def stop(self):
        if self.status == "stopped": return
        self.status = "stopping"; self._running = False
        self.send_command("stop")
        if self.process:
            try: self.process.wait(timeout=30)
            except subprocess.TimeoutExpired: self.process.kill()
        self.status = "stopped"
        self.sync_to_drive()

    def send_command(self, command: str) -> bool:
        if self.process and self.process.poll() is None and self.process.stdin:
            try: self.process.stdin.write(f"{command}\n".encode()); self.process.stdin.flush(); return True
            except: return False
        return False

    def get_status(self) -> dict:
        players, motd, latency, max_players = 0, "", 0, 20
        if self.status == "running":
            try:
                from mcstatus import JavaServer
                p = JavaServer("127.0.0.1", 25565).status()
                players, motd, latency = p.players.online, p.description, round(p.latency, 1)
                max_players = p.players.max
            except: pass
        # Read max-players from server.properties as fallback
        if max_players == 20:
            try:
                sp = self.local_path / "server.properties"
                if sp.exists():
                    for line in sp.read_text().splitlines():
                        if line.startswith("max-players="):
                            max_players = int(line.split("=", 1)[1].strip())
                            break
            except: pass
        # Build display name (underscores → spaces)
        display = self.server_name.replace("_", " ")
        return {"status": self.status, "players_online": players, "motd": motd,
                "latency_ms": latency, "server_type": self.server_type,
                "version": self.server_version, "server_name": self.server_name,
                "display_name": display,
                "max_players": max_players, "tps": self._parse_tps(),
                "jvm_mem": self.jvm_args.split("-Xmx")[-1].split()[0] if "-Xmx" in self.jvm_args else "?"}

    def _parse_tps(self) -> Optional[float]:
        """Parse TPS from recent log lines (Paper/Purpur format)."""
        for line in reversed(self.log_buffer[-200:]):
            m = re.search(r'TPS from last 1m.*?:\s*([\d.]+)', line)
            if m:
                return float(m.group(1))
        return None
