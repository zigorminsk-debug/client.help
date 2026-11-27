"""
updater.py — самообновление клиента из GitHub Releases.

Принцип:
  - опрашивается последний релиз репозитория: GET .../releases/latest;
  - если номер версии релиза больше текущей (APP_VERSION), предлагается обновление;
  - новый EXE скачивается во временный файл, затем запускается вспомогательный
    .bat, который переименовывает текущий EXE и заменяет его новым, после чего
    перезапускает приложение. Текущий процесс завершается, освобождая файл.

Размещение EXE: клиент должен лежать в пользовательском профиле
(%LOCALAPPDATA%\\CSL\\HelpdeskClient), иначе стандартный пользователь не сможет
его перезаписать (нужны права администратора). GPO-логон-скрипт копирует EXE
именно туда — см. deploy/Install-HelpdeskClient.ps1.
"""

import subprocess
import sys
import tempfile

import requests


def get_latest_release(repo: str, token: str = None,
                       base_url: str = "https://api.github.com", timeout: int = 15) -> dict:
    """Возвращает JSON последнего релиза или None (если релизов нет)."""
    url = f"{base_url.rstrip('/')}/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=headers, timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def parse_version(tag: str) -> int:
    """'v12' -> 12, '1.0.12' -> 12 (берём последний числовой сегмент)."""
    digits = "".join(ch if ch.isdigit() else "." for ch in (tag or ""))
    parts = [p for p in digits.split(".") if p]
    try:
        return int(parts[-1])
    except Exception:
        return 0


def is_newer(remote_tag: str, current_version: str) -> bool:
    try:
        return parse_version(remote_tag) > int(current_version or 0)
    except Exception:
        return False


def find_exe_asset(release: dict) -> dict:
    for a in release.get("assets", []):
        if str(a.get("name", "")).lower().endswith(".exe"):
            return a
    return None


def download(url: str, dest: str, token: str = None, timeout: int = 120):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=headers, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)


def perform_update(download_url: str, token: str = None) -> bool:
    """
    Скачивает новый EXE и заменяет текущий. Возвращает True, если замена запущена.
    После вызова приложение должно завершиться (root.quit(); sys.exit(0)).
    """
    current = sys.executable
    tmp = tempfile.mktemp(prefix="csl_upd_", suffix=".exe")
    download(download_url, tmp, token)

    bat = tempfile.mktemp(prefix="csl_upd_", suffix=".bat")
    cur = current.replace('"', '""')
    t = tmp.replace('"', '""')
    with open(bat, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        f.write("timeout /t 1 /nobreak >nul\n")
        f.write(f'if exist "{cur}.old" del /F /Q "{cur}.old"\n')
        f.write(f'move /Y "{cur}" "{cur}.old"\n')
        f.write(f'copy /Y "{t}" "{cur}"\n')
        f.write(f'start "" "{cur}"\n')
        f.write(f'del /F /Q "{t}"\n')
        f.write(f'del /F /Q "{bat}"\n')

    # Запускаем detached, чтобы процесс обновления пережил завершение приложения.
    subprocess.Popen(
        ["cmd", "/c", bat],
        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        close_fds=True,
    )
    return True
