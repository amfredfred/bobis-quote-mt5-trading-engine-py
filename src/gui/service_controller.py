"""
src/gui/service_controller.py — Windows service control for the engine.

Uses sc.exe (built-in) to query, start, stop and restart the
apex-quant-trader-agent service.  Falls back to in-process engine if the
service is not installed (development / first-run mode).
"""
from __future__ import annotations

import logging
import subprocess
import threading
from typing import Callable

logger = logging.getLogger(__name__)

SERVICE_NAME = "apex-quant-trader-agent"


# ── Status dataclass ──────────────────────────────────────────────────────────

class ServiceStatus:
    NOT_INSTALLED = "not_installed"
    STOPPED       = "stopped"
    STARTING      = "starting"
    RUNNING       = "running"
    STOPPING      = "stopping"
    UNKNOWN       = "unknown"


# ── Controller ────────────────────────────────────────────────────────────────

class ServiceController:
    """
    Thin wrapper around sc.exe for start / stop / status.

    All blocking calls are run in daemon threads so the GUI stays responsive.
    on_status_change(status: str, detail: str | None) is called from those
    threads — use app.after() in the callback to push updates to CTk.
    """

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name
        self.on_status_change: Callable[[str, str | None], None] | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def query(self) -> str:
        """Return current ServiceStatus.* value (synchronous, fast)."""
        try:
            result = subprocess.run(
                ["sc", "query", self.service_name],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            out = result.stdout.upper()
            if "DOES NOT EXIST" in out or result.returncode == 1060:
                return ServiceStatus.NOT_INSTALLED
            if "RUNNING" in out:
                return ServiceStatus.RUNNING
            if "STOPPED" in out:
                return ServiceStatus.STOPPED
            if "START_PENDING" in out:
                return ServiceStatus.STARTING
            if "STOP_PENDING" in out:
                return ServiceStatus.STOPPING
            return ServiceStatus.UNKNOWN
        except Exception as exc:
            logger.debug("sc query error: %s", exc)
            return ServiceStatus.UNKNOWN

    def is_installed(self) -> bool:
        return self.query() != ServiceStatus.NOT_INSTALLED

    def start(self) -> None:
        threading.Thread(target=self._do_start, daemon=True).start()

    def stop(self) -> None:
        threading.Thread(target=self._do_stop, daemon=True).start()

    def restart(self) -> None:
        threading.Thread(target=self._do_restart, daemon=True).start()

    def install(self, config_path: str) -> None:
        """Run install_service.ps1 to register the NSSM service."""
        threading.Thread(
            target=self._do_install, args=(config_path,), daemon=True
        ).start()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _notify(self, status: str, detail: str | None = None) -> None:
        if self.on_status_change:
            try:
                self.on_status_change(status, detail)
            except Exception:
                pass

    def _sc(self, *args: str, timeout: int = 30) -> tuple[int, str]:
        try:
            r = subprocess.run(
                ["sc", *args],
                capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return -1, "timeout"
        except Exception as exc:
            return -1, str(exc)

    def _do_start(self) -> None:
        self._notify(ServiceStatus.STARTING)
        # Re-enable auto-start so NSSM resumes its restart policy when the
        # engine is intentionally running (stop sets this to demand).
        self._sc("config", self.service_name, "start=", "auto", timeout=10)
        code, out = self._sc("start", self.service_name, timeout=20)
        if code == 0 or "RUNNING" in out.upper():
            self._notify(ServiceStatus.RUNNING)
        else:
            detail = out.strip().splitlines()[-1] if out.strip() else "unknown error"
            self._notify(ServiceStatus.STOPPED, detail)
            logger.warning("Service start failed: %s", out)

    def _do_stop(self) -> None:
        self._notify(ServiceStatus.STOPPING)
        # Disable auto-restart BEFORE sending the stop signal so NSSM does not
        # immediately bring the service back up (NSSM ignores a plain sc stop
        # and treats it as a crash unless the start type is set to demand first).
        self._sc("config", self.service_name, "start=", "demand", timeout=10)
        code, out = self._sc("stop", self.service_name, timeout=20)
        if code == 0 or "STOPPED" in out.upper():
            self._notify(ServiceStatus.STOPPED)
        else:
            detail = out.strip().splitlines()[-1] if out.strip() else "unknown error"
            self._notify(ServiceStatus.UNKNOWN, detail)
            logger.warning("Service stop failed: %s", out)

    def _do_restart(self) -> None:
        self._do_stop()
        import time; time.sleep(1)
        self._do_start()

    def _do_install(self, config_path: str) -> None:
        import sys
        from pathlib import Path
        self._notify(ServiceStatus.UNKNOWN, "Installing service…")

        # Search for install_service.ps1 by walking up from the exe.
        # Covers both packaged layout (exe in dist/xxx/) and dev layout.
        script: Path | None = None
        exe_dir = Path(sys.executable).resolve().parent
        for depth in range(6):
            candidate = exe_dir
            for _ in range(depth):
                candidate = candidate.parent
            ps1 = candidate / "install_service.ps1"
            if ps1.exists():
                script = ps1
                break
        if script is None:
            # Last resort: CWD
            cwd_ps1 = Path("install_service.ps1")
            if cwd_ps1.exists():
                script = cwd_ps1

        if script is None:
            self._notify(
                ServiceStatus.NOT_INSTALLED,
                "install_service.ps1 not found — run it manually as Administrator",
            )
            return

        logger.info("Running install_service.ps1: %s", script)
        try:
            r = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(script),
                    "-Action", "install",
                ],
                capture_output=True, text=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                self._notify(ServiceStatus.STOPPED, "Installed — click Start")
            else:
                detail = (r.stderr or r.stdout).strip().splitlines()[-1][:120]
                self._notify(ServiceStatus.NOT_INSTALLED, detail)
        except Exception as exc:
            self._notify(ServiceStatus.NOT_INSTALLED, str(exc)[:120])
