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
import socket
import subprocess

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
# AnyDesk / RustDesk — попытка автоопределения ID (с ручным вводом в GUI)
# --------------------------------------------------------------------------
def get_anydesk_id() -> str:
    """AnyDesk умеет отдавать свой ID через CLI: anydesk.exe --get-id"""
    for exe in ("anydesk.exe", "anydesk"):
        try:
            out = subprocess.run([exe, "--get-id"], capture_output=True, text=True,
                                 timeout=5, creationflags=0x08000000 if os.name == "nt" else 0)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            continue
    # Запасной вариант — реестр
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\AnyDesk") as key:
            for i in range(64):
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    if name.lower() == "adanywhereid" or (isinstance(value, str) and value.isdigit()):
                        return value
                except OSError:
                    break
    except Exception:
        pass
    return ""


def get_rustdesk_id() -> str:
    """RustDesk хранит ID в конфиге TOML."""
    candidates = [
        os.path.join(os.environ.get("ProgramData", ""), "RustDesk", "config", "RustDesk.toml"),
        os.path.expanduser("~/.config/rustdesk/RustDesk.toml"),
        os.path.expanduser("~/AppData/Roaming/RustDesk/config/RustDesk.toml"),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("id") and "=" in line:
                        val = line.split("=", 1)[1].strip().strip('"')
                        if val:
                            return val
        except Exception:
            continue
    return ""


if __name__ == "__main__":
    import json
    data = get_system_info()
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
