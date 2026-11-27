"""
system_info.py — сбор сведений о компьютере.

Кроссплатформенная часть (CPU/RAM/диски/сеть) работает везде через psutil.
Windows-специфичная часть (материнская плата, серийник, BIOS, видеокарта,
AnyDesk/RustDesk) использует WMI / реестр / файлы и корректно отключается
на других ОС. Это позволяет тестировать логику на Linux, а на Windows
получать полную картину.
"""

import datetime
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path

import psutil


# --------------------------------------------------------------------------
# Базовая информация (кроссплатформенно)
# --------------------------------------------------------------------------
def get_system_info() -> dict:
    info: dict = {}
    info["hostname"] = socket.gethostname()
    info["username"] = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    info["userdomain"] = os.environ.get("USERDOMAIN") or ""
    info["os"] = f"{platform.system()} {platform.release()} ({platform.version()})"
    info["arch"] = platform.machine()
    try:
        bt = psutil.boot_time()
        info["boot_time"] = datetime.datetime.fromtimestamp(bt).strftime("%Y-%m-%d %H:%M:%S")
        info["uptime_hours"] = round((datetime.datetime.now().timestamp() - bt) / 3600, 1)
    except Exception:
        info["uptime_hours"] = None

    # CPU
    cpu_freq = psutil.cpu_freq()
    info["cpu"] = {
        "model": platform.processor() or _first_cpu_name() or "Неизвестно",
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "max_freq_mhz": round(cpu_freq.max) if cpu_freq else None,
        "usage_percent": psutil.cpu_percent(interval=0.5),
    }

    # RAM
    vm = psutil.virtual_memory()
    info["ram"] = {
        "total_gb": round(vm.total / 1024 ** 3, 2),
        "available_gb": round(vm.available / 1024 ** 3, 2),
        "used_percent": vm.percent,
    }

    # Диски
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": round(usage.total / 1024 ** 3, 2),
                "free_gb": round(usage.free / 1024 ** 3, 2),
                "used_percent": usage.percent,
            })
        except Exception:
            continue
    info["disks"] = disks

    # Материнская плата / BIOS / серийник (Windows via WMI)
    info["motherboard"] = _motherboard_info()
    info["bios"] = _bios_info()
    info["gpu"] = _gpu_info()
    info["network"] = _network_info()
    return info


# --------------------------------------------------------------------------
# Форматирование для вставки в описание заявки (Atlassian wiki markup)
# --------------------------------------------------------------------------
def format_system_info_markup(info: dict) -> str:
    lines = []
    lines.append("h2. Сведения о системе")
    lines.append(f"*Хост:* {info.get('hostname', '')}")
    lines.append(f"*Пользователь:* {info.get('username', '')}@{info.get('userdomain', '')}")
    lines.append(f"*ОС:* {info.get('os', '')} ({info.get('arch', '')})")
    if info.get("uptime_hours") is not None:
        lines.append(f"*Аптайм:* {info.get('uptime_hours')} ч")
    lines.append("")
    cpu = info.get("cpu", {})
    lines.append("h3. Процессор")
    lines.append(f"*Модель:* {cpu.get('model', '?')}")
    lines.append(f"*Ядра:* {cpu.get('physical_cores')} физ. / {cpu.get('logical_cores')} лог.")
    if cpu.get("max_freq_mhz"):
        lines.append(f"*Частота (макс):* {cpu.get('max_freq_mhz')} МГц")
    lines.append(f"*Загрузка CPU:* {cpu.get('usage_percent')}%")
    lines.append("")
    ram = info.get("ram", {})
    lines.append("h3. Память")
    lines.append(f"*Всего:* {ram.get('total_gb')} ГБ")
    lines.append(f"*Свободно:* {ram.get('available_gb')} ГБ")
    lines.append(f"*Использовано:* {ram.get('used_percent')}%")
    lines.append("")
    lines.append("h3. Диски")
    for d in info.get("disks", []):
        lines.append(
            f"* {d.get('device')} ({d.get('fstype')}) — всего {d.get('total_gb')} ГБ, "
            f"свободно {d.get('free_gb')} ГБ ({d.get('used_percent')}%)"
        )
    lines.append("")
    mb = info.get("motherboard", {})
    if mb:
        lines.append("h3. Материнская плата")
        for k, v in mb.items():
            if v:
                lines.append(f"*{k}:* {v}")
        lines.append("")
    bios = info.get("bios", {})
    if bios:
        lines.append("h3. BIOS / Система")
        for k, v in bios.items():
            if v:
                lines.append(f"*{k}:* {v}")
        lines.append("")
    gpu = info.get("gpu", [])
    if gpu:
        lines.append("h3. Видеоадаптеры")
        for g in gpu:
            lines.append(f"* {g}")
        lines.append("")
    net = info.get("network", [])
    if net:
        lines.append("h3. Сетевые интерфейсы")
        for n in net:
            lines.append(f"* {n}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Windows-специфичные данные (WMI) — с защитой от ошибок на других ОС
# --------------------------------------------------------------------------
def _wmi_query(wql: str):
    try:
        import wmi
    except Exception:
        return []
    try:
        c = wmi.WMI()
        return c.query(wql)
    except Exception:
        return []


def _first_cpu_name() -> str:
    rows = _wmi_query("SELECT Name FROM Win32_Processor")
    if rows:
        return str(rows[0].Name).strip()
    return ""


def _motherboard_info() -> dict:
    out = {}
    for row in _wmi_query("SELECT Manufacturer, Product, SerialNumber FROM Win32_BaseBoard"):
        out["Производитель"] = getattr(row, "Manufacturer", "")
        out["Модель"] = getattr(row, "Product", "")
        out["Серийный №"] = getattr(row, "SerialNumber", "")
        break
    # Серийник корпуса/системы
    for row in _wmi_query("SELECT SerialNumber, Manufacturer, Model FROM Win32_ComputerSystem"):
        if not out.get("Серийный №"):
            out["Серийный №"] = getattr(row, "SerialNumber", "")
        out["Производитель ПК"] = getattr(row, "Manufacturer", "")
        out["Модель ПК"] = getattr(row, "Model", "")
        break
    return {k: v for k, v in out.items() if v}


def _bios_info() -> dict:
    out = {}
    for row in _wmi_query("SELECT Manufacturer, SMBIOSBIOSVersion, ReleaseDate FROM Win32_BIOS"):
        out["BIOS"] = getattr(row, "Manufacturer", "")
        out["Версия BIOS"] = getattr(row, "SMBIOSBIOSVersion", "")
        break
    return {k: v for k, v in out.items() if v}


def _gpu_info() -> list:
    out = []
    for row in _wmi_query("SELECT Name, AdapterRAM FROM Win32_VideoController"):
        name = getattr(row, "Name", "")
        ram = getattr(row, "AdapterRAM", None)
        if name:
            txt = name
            if ram:
                try:
                    txt += f" ({round(int(ram) / 1024 ** 3, 1)} ГБ)"
                except Exception:
                    pass
            out.append(txt)
    return out


def _network_info() -> list:
    out = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    for name, snics in addrs.items():
        if name in ("lo", "Loopback Pseudo-Interface 1"):
            continue
        st = stats.get(name)
        if not st or not st.isup:
            continue
        ips = [s.address for s in snics if s.family == socket.AF_INET]
        if ips:
            out.append(f"{name}: {', '.join(ips)}")
    return out


# --------------------------------------------------------------------------
# AnyDesk / RustDesk — автоопределение ID.
#
# Раньше детект почти всегда возвращал "" на реальных ПК:
#  - AnyDesk искался только в PATH (anydesk.exe), а он лежит в
#    C:\Program Files (x86)\AnyDesk\AnyDesk.exe и в PATH его нет;
#  - реестр HKLM\SOFTWARE\AnyDesk ID обычно не содержит (он в anydesk.conf);
#  - RustDesk.toml пишет id = '123456789' в одинарных кавычках, а старый
#    парсер снимал только двойные и возвращал ID вместе с кавычками.
#
# Теперь источники проверяются по порядку (быстрые и надёжные — первыми):
#  AnyDesk:  anydesk.conf (ad_anywhere_id) -> CLI --get-id по полному пути
#            (Program Files / реестр Uninstall / запущенный процесс) -> реестр.
#  RustDesk: RustDesk.toml (%APPDATA% / %LOCALAPPDATA% / %PROGRAMDATA% /
#            рядом с exe / ~/.config) -> CLI --get-id -> реестр.
# Чистые функции разбора (parse_*/validate_*) вынесены отдельно и покрыты
# тестами на Linux; Windows-специфичные ветки корректно отключаются вне Windows.
# --------------------------------------------------------------------------
_REMOTE_ID_RE = re.compile(r"\d{6,15}")


def validate_remote_id(raw) -> str:
    """Выделить из произвольного значения правдоподобный ID (6–15 цифр).

    AnyDesk/RustDesk ID — только цифры (обычно 9). Возвращает "" если
    правдоподобного ID нет. Снимает окружающие кавычки/пробелы.
    """
    if raw is None:
        return ""
    s = str(raw).strip().strip("'\" \t\r\n")
    if not s:
        return ""
    m = _REMOTE_ID_RE.search(s)
    return m.group(0) if m else ""


def parse_anydesk_conf(text: str) -> str:
    """Разобрать anydesk.conf: строка ad_anywhere_id = 123456789."""
    if not text:
        return ""
    m = re.search(r"ad_anywhere_id\s*=\s*['\"]?(\d{6,15})['\"]?", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"^\s*id\s*=\s*['\"]?(\d{6,15})['\"]?\s*$", text,
                  re.IGNORECASE | re.MULTILINE)
    if m:
        return m.group(1)
    return ""


def parse_rustdesk_toml(text: str) -> str:
    """Разобрать RustDesk.toml: id = '123456789' (одинарные/двойные/без кавычек)."""
    if not text:
        return ""
    m = re.search(r"^\s*id\s*=\s*['\"]?(\d{6,15})['\"]?\s*$", text, re.MULTILINE)
    if m:
        return m.group(1)
    return ""


def extract_cli_id(stdout: str) -> str:
    """Выделить ID из вывода `*.exe --get-id` (там просто число + перевод строки)."""
    if not stdout:
        return ""
    m = _REMOTE_ID_RE.search(stdout)
    return m.group(0) if m else ""


def _dedupe_paths(paths) -> list:
    seen = set()
    out = []
    for p in paths:
        if not p:
            continue
        try:
            key = str(p).lower()
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(str(p))
    return out


def _running_process_exes(*needles: str) -> list:
    """Пути к exe запущенных процессов, чьё имя содержит needle (AnyDesk/RustDesk)."""
    out = []
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                exe = proc.info.get("exe") or ""
                name = (proc.info.get("name") or "").lower()
                if exe and any(n in name for n in needles):
                    out.append(exe)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _registry_uninstall_exe(product: str) -> str:
    """Найти путь к exe через Uninstall-ключи (InstallLocation/DisplayIcon/Uninstall)."""
    try:
        import winreg
    except Exception:
        return ""
    hives = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    suffixes = [
        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{product}",
        rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{product}",
    ]
    for hive in hives:
        for suffix in suffixes:
            try:
                with winreg.OpenKey(hive, suffix) as key:
                    for value_name in ("InstallLocation", "DisplayIcon", "Uninstall"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                        except Exception:
                            continue
                        s = str(value or "")
                        # DisplayIcon/Uninstall: "C:\...\AnyDesk.exe,0" или '"C:\...\AnyDesk.exe" /S'
                        s = s.split(",")[0].strip().strip('"')
                        # В Uninstall может быть строка с аргументами — взять до пробела вне кавычек.
                        m = re.search(r"[A-Za-z]:\\[^\"]*?\.exe", s)
                        candidate = m.group(0) if m else s
                        if candidate.lower().endswith(".exe") and os.path.isfile(candidate):
                            return candidate
            except Exception:
                continue
    return ""


def _candidate_anydesk_exes() -> list:
    cands = []
    for name in ("anydesk.exe", "anydesk"):
        try:
            found = shutil.which(name)
        except Exception:
            found = None
        if found:
            cands.append(found)
    for env_name, default in (("ProgramFiles", r"C:\Program Files"),
                              ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                              ("LOCALAPPDATA", ""), ("APPDATA", "")):
        base = os.environ.get(env_name) or default
        if base:
            cands.append(os.path.join(base, "AnyDesk", "AnyDesk.exe"))
    loc = _registry_uninstall_exe("AnyDesk")
    if loc:
        cands.append(loc)
    cands.extend(_running_process_exes("anydesk"))
    # Оставляем только реально существующие файлы (which() тоже даёт существующий).
    return [p for p in _dedupe_paths(cands) if os.path.isfile(p)]


def _candidate_rustdesk_exes() -> list:
    cands = []
    for name in ("rustdesk.exe", "rustdesk"):
        try:
            found = shutil.which(name)
        except Exception:
            found = None
        if found:
            cands.append(found)
    for env_name, default in (("ProgramFiles", r"C:\Program Files"),
                              ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                              ("LOCALAPPDATA", ""), ("APPDATA", "")):
        base = os.environ.get(env_name) or default
        if base:
            cands.append(os.path.join(base, "RustDesk", "rustdesk.exe"))
    loc = _registry_uninstall_exe("RustDesk")
    if loc:
        cands.append(loc)
    cands.extend(_running_process_exes("rustdesk"))
    return [p for p in _dedupe_paths(cands) if os.path.isfile(p)]


def _candidate_anydesk_confs(exes=None) -> list:
    cands = []
    for env_name, default in (("ProgramData", r"C:\ProgramData"),
                              ("APPDATA", ""), ("LOCALAPPDATA", "")):
        base = os.environ.get(env_name) or default
        if base:
            cands.append(os.path.join(base, "AnyDesk", "anydesk.conf"))
    for exe in (exes or []):
        try:
            cands.append(os.path.join(os.path.dirname(exe), "anydesk.conf"))
        except Exception:
            continue
    cands.append(os.path.expanduser("~/.config/anydesk/anydesk.conf"))
    cands.append(os.path.expanduser("~/.anydesk/anydesk.conf"))
    return _dedupe_paths(cands)


def _candidate_rustdesk_confs(exes=None) -> list:
    cands = []
    for env_name, default in (("APPDATA", ""), ("LOCALAPPDATA", ""),
                              ("ProgramData", r"C:\ProgramData")):
        base = os.environ.get(env_name) or default
        if base:
            cands.append(os.path.join(base, "RustDesk", "config", "RustDesk.toml"))
    for exe in (exes or []):
        try:
            d = os.path.dirname(exe)
            cands.append(os.path.join(d, "RustDesk.toml"))
            cands.append(os.path.join(d, "config", "RustDesk.toml"))
        except Exception:
            continue
    cands.append(os.path.expanduser("~/.config/rustdesk/RustDesk.toml"))
    # Легаси-путь через ~ (на случай, если APPDATA не задан).
    cands.append(os.path.expanduser("~/AppData/Roaming/RustDesk/config/RustDesk.toml"))
    return _dedupe_paths(cands)


def _read_file_id(path: str, parser) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    try:
        return parser(text) or ""
    except Exception:
        return ""


def _run_cli_get_id(exe: str) -> str:
    """Запустить `<exe> --get-id` без окна и вернуть ID (или "")."""
    try:
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        out = subprocess.run([exe, "--get-id"], capture_output=True, text=True,
                             timeout=4, creationflags=flags)
        if out.returncode == 0:
            return extract_cli_id(out.stdout or "")
    except Exception:
        pass
    return ""


def _read_registry_id(paths) -> str:
    """Найти ID среди значений реестра (ключи с 'id' в имени — в первую очередь)."""
    try:
        import winreg
    except Exception:
        return ""
    fallback = ""
    for hive, path in paths:
        try:
            with winreg.OpenKey(hive, path) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    vid = validate_remote_id(value)
                    if not vid:
                        continue
                    if "id" in str(name).lower():
                        return vid
                    if not fallback:
                        fallback = vid
        except Exception:
            continue
    return fallback


def get_anydesk_id_with_source() -> tuple:
    """(id, источник) — источник для подписи в GUI, напр. 'конфиг ...'."""
    exes = _candidate_anydesk_exes()
    # 1) Конфиг — быстрый и не требует запуска AnyDesk.
    for path in _candidate_anydesk_confs(exes):
        vid = _read_file_id(path, parse_anydesk_conf)
        if vid:
            return vid, f"конфиг {path}"
    # 2) CLI по полному пути к exe.
    for exe in exes:
        vid = _run_cli_get_id(exe)
        if vid:
            return vid, "командная строка AnyDesk"
    # 3) Реестр (запасной вариант).
    try:
        import winreg
        vid = _read_registry_id([
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\AnyDesk"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\AnyDesk"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\AnyDesk"),
        ])
        if vid:
            return vid, "реестр"
    except Exception:
        pass
    return "", ""


def get_rustdesk_id_with_source() -> tuple:
    """(id, источник) — источник для подписи в GUI."""
    exes = _candidate_rustdesk_exes()
    # 1) TOML-конфиг (основной и надёжный источник).
    for path in _candidate_rustdesk_confs(exes):
        vid = _read_file_id(path, parse_rustdesk_toml)
        if vid:
            return vid, f"конфиг {path}"
    # 2) CLI.
    for exe in exes:
        vid = _run_cli_get_id(exe)
        if vid:
            return vid, "командная строка RustDesk"
    # 3) Реестр (запасной вариант).
    try:
        import winreg
        vid = _read_registry_id([
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\RustDesk"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\RustDesk"),
        ])
        if vid:
            return vid, "реестр"
    except Exception:
        pass
    return "", ""


def get_anydesk_id() -> str:
    """ID AnyDesk ("" если не удалось определить)."""
    vid, _ = get_anydesk_id_with_source()
    return vid


def get_rustdesk_id() -> str:
    """ID RustDesk ("" если не удалось определить)."""
    vid, _ = get_rustdesk_id_with_source()
    return vid


def detect_remote_ids() -> dict:
    """Оба ID + источники — для асинхронного заполнения полей в GUI."""
    a_id, a_src = get_anydesk_id_with_source()
    r_id, r_src = get_rustdesk_id_with_source()
    return {
        "anydesk": {"id": a_id, "source": a_src},
        "rustdesk": {"id": r_id, "source": r_src},
    }


if __name__ == "__main__":
    import json
    data = get_system_info()
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
