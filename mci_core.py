"""
mci_core.py — MineColab Improved v2
Manages the Minecraft server process, local storage and Drive sync.
"""

import os
import shutil
import subprocess
import threading
import time
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger("mci_core")


# ─────────────────────────────────────────────
#  JVM FLAG PRESETS
# ─────────────────────────────────────────────

def _aikars_flags(mem: str) -> str:
    """Aikar's flags for Paper / Purpur – https://mcflags.emc.gs"""
    return (
        f"-Xms{mem} -Xmx{mem} "
        "-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 "
        "-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC "
        "-XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 "
        "-XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M "
        "-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 "
        "-XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 "
        "-XX:G1MixedGCLiveThresholdPercent=90 "
        "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 "
        "-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 "
        "-Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true"
    )


def _velocity_flags(mem: str) -> str:
    return (
        f"-Xms{mem} -Xmx{mem} "
        "-XX:+UseG1GC -XX:G1HeapRegionSize=4M "
        "-XX:+UnlockExperimentalVMOptions -XX:+ParallelRefProcEnabled "
        "-XX:+AlwaysPreTouch -XX:MaxInlineLevel=15"
    )


def _generic_flags(mem: str) -> str:
    return f"-Xms{mem} -Xmx{mem} -XX:+UseG1GC -XX:+AlwaysPreTouch"


def build_jvm_flags(server_type: str, mem: str = "10G") -> str:
    if server_type in ("paper", "purpur", "arclight", "folia"):
        return _aikars_flags(mem)
    if server_type == "velocity":
        return _velocity_flags(mem)
    return _generic_flags(mem)


# ─────────────────────────────────────────────
#  MINECRAFT SERVER CLASS
# ─────────────────────────────────────────────

class MinecraftServer:
    """
    Runs Minecraft from local disk, streams logs, syncs with Drive.
    """

    def __init__(
        self,
        server_name: str,
        drive_path: str,
        local_path: str,
        server_type: str,
        server_version: str,
        jvm_mem: str = "10G",
        custom_jvm_args: Optional[str] = None,
        sync_interval: int = 300,
    ):
        self.server_name = server_name
        self.drive_path = Path(drive_path)
        self.local_path = Path(local_path)
        self.server_type = server_type
        self.server_version = server_version
        self.jvm_args = custom_jvm_args or build_jvm_flags(server_type, jvm_mem)
        self.sync_interval = sync_interval

        self.process: Optional[subprocess.Popen] = None
        self.status: str = "stopped"  # stopped | starting | running | stopping
        self._running: bool = False

        self._log_callbacks: List[Callable[[str], None]] = []
        self._async_log_queue: Optional[asyncio.Queue] = None
        self.log_buffer: List[str] = []
        self.MAX_BUFFER = 2000

        self._log_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

    # ── Log helpers ───────────────────────────

    def set_async_queue(self, queue: asyncio.Queue):
        """Attach an asyncio queue for FastAPI WebSocket forwarding."""
        self._async_log_queue = queue

    def add_log_callback(self, cb: Callable[[str], None]):
        self._log_callbacks.append(cb)

    def _emit(self, line: str):
        self.log_buffer.append(line)
        if len(self.log_buffer) > self.MAX_BUFFER:
            self.log_buffer = self.log_buffer[-self.MAX_BUFFER:]
        for cb in self._log_callbacks:
            try:
                cb(line)
            except Exception:
                pass
        if self._async_log_queue is not None:
            try:
                self._async_log_queue.put_nowait(line)
            except asyncio.QueueFull:
                pass

    # ── Drive sync ────────────────────────────

    def sync_from_drive(self):
        """Copy / rsync server files from Drive → local VM disk."""
        src = str(self.drive_path / self.server_name) + "/"
        dst = str(self.local_path) + "/"
        self.local_path.mkdir(parents=True, exist_ok=True)

        self._emit("[MCI] ⬇  Syncing from Google Drive…")
        rc = os.system(
            f'rsync -a --update '
            f'--exclude="logs/" '
            f'--exclude="*.log" '
            f'--exclude="session.lock" '
            f'"{src}" "{dst}" 2>/dev/null'
        )
        if rc != 0:
            self._emit("[MCI] rsync unavailable — falling back to shutil copy…")
            src_path = self.drive_path / self.server_name
            if src_path.exists():
                shutil.copytree(str(src_path), str(self.local_path), dirs_exist_ok=True)
        self._emit("[MCI] ✅ Sync from Drive complete.")

    def sync_to_drive(self):
        """Sync local VM disk → Drive (incremental)."""
        src = str(self.local_path) + "/"
        dst = str(self.drive_path / self.server_name) + "/"
        (self.drive_path / self.server_name).mkdir(parents=True, exist_ok=True)

        self._emit("[MCI] ⬆  Syncing to Google Drive…")
        rc = os.system(
            f'rsync -a --update '
            f'--exclude="logs/debug.log" '
            f'"{src}" "{dst}" 2>/dev/null'
        )
        if rc != 0:
            self._emit("[MCI] rsync unavailable — falling back to shutil copy…")
            shutil.copytree(str(self.local_path), str(self.drive_path / self.server_name), dirs_exist_ok=True)
        self._emit("[MCI] ✅ Sync to Drive complete.")

    def _sync_loop(self):
        while self._running:
            time.sleep(self.sync_interval)
            if self._running and self.status == "running":
                try:
                    self.sync_to_drive()
                except Exception as e:
                    self._emit(f"[MCI] ⚠ Auto-sync error: {e}")

    # ── Backup ────────────────────────────────

    def backup_world(self) -> str:
        """Backup world folders to Drive/backup/world/."""
        backup_base = self.drive_path / "backup" / "world"
        backup_base.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_name = f"{self.server_name}_world_{ts}"
        backup_path = backup_base / backup_name
        backup_path.mkdir()

        worlds = ["world", "world_nether", "world_the_end", "worlds"]
        backed = []
        for w in worlds:
            src = self.local_path / w
            if src.exists():
                shutil.copytree(str(src), str(backup_path / w))
                backed.append(w)

        self._emit(f"[MCI] 💾 Backup saved: {backup_name}  ({', '.join(backed)})")
        return str(backup_path)

    # ── Java detection ────────────────────────

    @staticmethod
    def _required_java(server_type: str, version: str) -> int:
        try:
            parts = [int(p) for p in version.split(".")]
            # Detectamos si es formato viejo (1.20.1) o nuevo (26.1)
            if parts[0] == 1:
                major = parts[1] if len(parts) > 1 else 0
                patch = parts[2] if len(parts) > 2 else 0
            else:
                # En versiones nuevas (ej. 26.1), el primer número es el "major"
                major = parts[0]
                patch = parts[1] if len(parts) > 1 else 0
        except Exception:
            major, patch = 0, 0
    
        # Casos especiales de software de servidor
        if server_type == "velocity":
            return 17
        
        if server_type == "neoforge":
            return 21
    
        if server_type == "mohist":
            if major <= 12: return 8
            if major <= 16: return 11
            return 17
    
        # Lógica de versiones de Minecraft (Vanilla/Paper/Forge/etc)
        # A partir de la 1.20.5 (y todas las versiones 21, 22... 26+)
        if (major == 20 and patch >= 5) or major >= 21:
            return 21
        
        # De la 1.17 hasta la 1.20.4
        if major >= 17:
            return 17
            
        # Versiones antiguas
        return 8

    def install_java(self):
        """Install the correct OpenJDK version if not present."""
        jver = self._required_java(self.server_type, self.server_version)
        self._emit(f"[MCI] 🔧 Checking Java {jver}…")
        rc = os.system(
            f"java -version 2>&1 | grep -q '\"{ jver }\\.' || "
            f"(sudo apt-get update -qq && "
            f"sudo apt-get install -y -qq openjdk-{jver}-jdk-headless && "
            f"sudo update-alternatives --install /usr/bin/java java "
            f"/usr/lib/jvm/java-{jver}-openjdk-amd64/bin/java 1)"
        )
        if rc != 0:
            self._emit(f"[MCI] ⚠ Java {jver} install may have failed.")
        else:
            self._emit(f"[MCI] ✅ Java {jver} ready.")

    # ── Command builder ───────────────────────

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
            cmd = run_sh.read_text()
            if "java" in cmd:
                cmd = cmd[cmd.find("java"):]
                cmd = cmd.replace("@user_jvm_args.txt", self.jvm_args)
                cmd = cmd.replace('"$@"', 'nogui "$@"')
                return cmd.strip()

        if self.server_type == "bedrock":
            return "LD_LIBRARY_PATH=. ./bedrock_server"
        return f"java -server {self.jvm_args} -jar {self._jar_name()} nogui"

    # ── Log reader ────────────────────────────

    def _read_logs(self):
        if not self.process:
            return
        try:
            for raw in iter(self.process.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                self._emit(line)
                if "Done" in line and ("help" in line or "For help" in line):
                    self.status = "running"
                    self._emit("[MCI] 🟢 Server is online!")
        except Exception as e:
            self._emit(f"[MCI] Log reader error: {e}")
        self.process.stdout.close()

    # ── Watchdog ─────────────────────────────

    def _watchdog(self):
        """Detect when the MC process exits unexpectedly."""
        if self.process:
            self.process.wait()
        if self._running:
            self._emit("[MCI] ⚠ Minecraft process exited.")
            self.status = "stopped"
            self._running = False

    # ── Public API ────────────────────────────

    def start(self) -> bool:
        if self.status in ("running", "starting"):
            self._emit("[MCI] Server already running.")
            return False

        self.status = "starting"
        self._running = True

        # Remove stale session lock
        lock = self.local_path / "world" / "session.lock"
        if lock.exists():
            lock.unlink()

        # Accept EULA
        (self.local_path / "eula.txt").write_text("eula=true\n")

        if self.server_type not in ("bedrock",):
            self.install_java()

        cmd = self._build_command()
        self._emit(f"[MCI] 🚀 Launching: {cmd}")

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
        except Exception as e:
            self.status = "stopped"
            self._emit(f"[MCI] ❌ Launch failed: {e}")
            return False

        self._log_thread = threading.Thread(target=self._read_logs, daemon=True)
        self._log_thread.start()

        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()

        return True

    def stop(self):
        if self.status == "stopped":
            return
        self._emit("[MCI] 🛑 Sending stop command…")
        self.status = "stopping"
        self.send_command("stop")

        if self.process:
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._emit("[MCI] ⚠ Force-killing server…")
                self.process.kill()

        self._running = False
        self.status = "stopped"
        self._emit("[MCI] Server stopped.")
        self.sync_to_drive()

    def send_command(self, command: str) -> bool:
        if self.process and self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.write(f"{command}\n".encode())
                self.process.stdin.flush()
                return True
            except BrokenPipeError:
                return False
        return False

    def get_status(self) -> dict:
        players_online = 0
        motd = ""
        latency = 0

        if self.status == "running":
            try:
                from mcstatus import JavaServer
                srv = JavaServer("127.0.0.1", 25565)
                ping = srv.status()
                players_online = ping.players.online
                motd = ping.description
                latency = round(ping.latency, 1)
            except Exception:
                pass

        return {
            "status": self.status,
            "players_online": players_online,
            "motd": motd,
            "latency_ms": latency,
            "server_type": self.server_type,
            "version": self.server_version,
            "server_name": self.server_name,
        }
