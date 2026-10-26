"""Updates: the newest release on GitHub, and for the Windows app, installing it from Settings → Updates or the
tray (download the installer, run it silently; it stops Ferret, replaces it and starts it again)."""

import json
import os
import logging
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from importlib.metadata import version
from pathlib import Path

log = logging.getLogger(__name__)
RELEASES = "https://api.github.com/repos/MayankPunghal/ferret/releases/latest"
state = {"latest": None, "url": None, "checked_at": None, "error": None, "installing": None, "progress": None}


def current() -> str:
    return version("ferret-search")


def windows_app() -> bool:
    """Installed by Ferret-Setup.exe (its Python sits next to stop.ps1), so it can update itself."""
    return sys.platform == "win32" and (Path(sys.prefix).parent / "stop.ps1").is_file()


def newer(tag: str, than: str) -> bool:
    def parts(v):
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())
    return parts(tag) > parts(than)


def check() -> dict:
    """Ask GitHub for the newest release (and its Windows installer)."""
    try:
        req = urllib.request.Request(RELEASES, headers={"User-Agent": "Ferret", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.load(r)
        state.update(latest=rel["tag_name"].lstrip("v"), error=None,
                     url=next((a["browser_download_url"] for a in rel.get("assets", []) if a["name"].endswith(".exe")), None))
    except (OSError, ValueError, KeyError) as e:
        state["error"] = f"could not reach GitHub: {e}"
    state["checked_at"] = time.time()
    return status()


def status() -> dict:
    latest = state["latest"]
    return {"current": current(), "latest": latest, "newer": bool(latest and newer(latest, current())),
            "windows_app": windows_app(), "can_install": windows_app() and bool(state["url"]),
            "checked_at": state["checked_at"], "error": state["error"], "installing": state["installing"],
            "progress": state["progress"]}


RESTART_SCRIPT = r"""param([int]$WaitPid, [int]$InstallerPid, [string]$App, [string]$Pythonw, [string]$Data)
# Runs detached while the installer replaces Ferret, then starts Ferret again by itself. The installer also
# tries (its [Run] entry opens the Start-menu shortcut), so this checks first that nothing answers: if it does,
# there is nothing to do. Owner's report (2026-10-26): after an update the app stayed closed and the page sat on
# "Disconnected".
function Running {
  $pf = Join-Path $Data 'port'
  if (-not (Test-Path $pf)) { return $false }
  $p = (Get-Content $pf -TotalCount 1).Trim()
  if (-not $p) { return $false }
  try { return (Invoke-WebRequest "http://127.0.0.1:$p/healthz" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200 }
  catch { return $false }
}
try { Wait-Process -Id $WaitPid -Timeout 120 -ErrorAction SilentlyContinue } catch { }
try { Wait-Process -Id $InstallerPid -Timeout 1800 -ErrorAction SilentlyContinue } catch { }
Start-Sleep -Seconds 2
if (Running) { exit 0 }
$stop = Join-Path $App 'stop.ps1'
if (Test-Path $stop) { & $stop -App $App | Out-Null }
Start-Sleep -Seconds 1
Start-Process -FilePath $Pythonw -ArgumentList '-m', 'knowledge_search.tray', '--background' -WorkingDirectory $App -WindowStyle Hidden
$deadline = (Get-Date).AddSeconds(150)
while ((Get-Date) -lt $deadline) { if (Running) { exit 0 }; Start-Sleep -Seconds 3 }
# it did not come up: one more try, then leave the logs to say why
Start-Process -FilePath $Pythonw -ArgumentList '-m', 'knowledge_search.tray', '--background' -WorkingDirectory $App -WindowStyle Hidden
"""


def _restart_when_done(installer_pid: int) -> None:
    """A detached PowerShell that waits for this app and the installer to exit, then starts Ferret again.

    The installer cannot be trusted to restart it: it opens the Start-menu shortcut through Explorer, which
    fails silently when there is no shortcut or when Explorer refuses, and after an update the app then stayed
    closed (owner's report). This helper lives outside the install folder, so the folder's replacement cannot
    remove it while it waits."""
    if sys.platform != "win32":
        return
    app = Path(sys.prefix).parent
    if not (app / "stop.ps1").is_file():  # not the installed app (a dev checkout): nothing to restart
        return
    from .config import get_settings

    data = Path(get_settings().data_dir)
    script = data / "update-restart.ps1"
    try:
        data.mkdir(parents=True, exist_ok=True)
        script.write_text(RESTART_SCRIPT, encoding="utf-8")
        pythonw = Path(sys.prefix) / "pythonw.exe"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-File", str(script), "-WaitPid", str(os.getpid()), "-InstallerPid", str(installer_pid),
                          "-App", str(app), "-Pythonw", str(pythonw), "-Data", str(data)],
                         creationflags=flags, close_fds=True)
        log.warning("a restart helper is waiting for the installer (pid %s)", installer_pid)
    except OSError as e:  # the installer's own [Run] entry is still the fallback
        log.warning("could not start the restart helper: %s", e)


def install(on_started=None) -> None:
    """Download the newer installer and run it; `on_started` (the tray's quit) runs once it has started."""
    def run():
        target = Path(tempfile.gettempdir()) / f"Ferret-Setup-{state['latest']}.exe"
        try:
            state.update(installing="downloading", progress=0)
            req = urllib.request.Request(state["url"], headers={"User-Agent": "Ferret"})
            with urllib.request.urlopen(req, timeout=600) as r, open(target, "wb") as f:
                total, done = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    state["progress"] = done * 100 // total if total else None
        except OSError as e:
            state.update(installing=None, error=f"the update could not be downloaded: {e}")
            return
        state["installing"] = "installing"
        # the restarted app opens no new browser tab: the page that started the update reloads itself (tray.py)
        from .config import get_settings

        (Path(get_settings().data_dir) / "updating").write_text(state["latest"])
        log.warning("installing Ferret %s from %s", state["latest"], target)
        proc = subprocess.Popen([str(target), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"])
        _restart_when_done(proc.pid)  # starts Ferret again even when the installer's own [Run] entry fails
        if on_started:
            on_started()

    threading.Thread(target=run, daemon=True, name="update").start()


if __name__ == "__main__":
    assert newer("v1.0.2", "1.0.1") and not newer("v1.0.1", "1.0.1") and newer("1.1.0", "1.0.9")
    print("ok")
