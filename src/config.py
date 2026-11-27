"""
config.py — конфигурация клиента заявок CSL Helpdesk.

Два уровня настроек:

1) Машинный (административный) конфиг — общие для всей организации данные,
   в том числе ЕДИНЫЙ логин/пароль для Jira и настройки автообновления.
   Вводится администратором ОДИН раз при настройке групповой политики и
   разворачивается на все ПК (GPO). Источники (по приоритету):
     a) переменная окружения CSL_HELPDESK_CONFIG (для тестов);
     b) файл C:\\ProgramData\\CSL\\HelpdeskClient\\config.json (GPO-логон-скрипт);
     c) реестр HKLM\\SOFTWARE\\CSL\\Helpdesk (GPO Preferences).

2) Пользовательский конфиг — личные данные сотрудника (контактный телефон и т.п.).
   Хранится в %LOCALAPPDATA%\\CSL\\HelpdeskClient\\user.json (только для user).
"""

import json
import os
import sys
from pathlib import Path

VENDOR = "CSL"
APP_NAME = "HelpdeskClient"
APP_TITLE = "CSL — Заявка в техподдержку"

# Список неисправностей по умолчанию (используется, если не задан в админ-конфиге)
DEFAULT_FAULTS = [
    "Не включается / не загружается",
    "Не работает интернет / сеть",
    "Медленно работает",
    "Не работает принтер / сканер",
    "Не работает программа / ПО",
    "Проблема с электронной почтой",
    "Нужна установка / настройка ПО",
    "Прочее (описано в комментарии)",
]


def machine_config_path() -> Path:
    """Путь к административному конфигу, развёрнутому GPO."""
    prog = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(prog) / VENDOR / APP_NAME / "config.json"


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    p = Path(base) / VENDOR / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_data_file() -> Path:
    return user_data_dir() / "user.json"


def _read_registry_config() -> dict:
    """Чтение общих настроек из HKLM (GPO Preferences). Только на Windows."""
    try:
        import winreg  # модуль доступен только на Windows
    except Exception:
        return {}
    result = {}
    path = r"SOFTWARE\CSL\Helpdesk"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    result[name.lower()] = value
                    i += 1
                except OSError:
                    break
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return result


def _config_candidate_paths():
    """Пути к административному config.json в порядке поиска."""
    prog = os.environ.get("ProgramData", r"C:\ProgramData")
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        exe_dir = ""
    candidates = [
        os.path.join(prog, VENDOR, APP_NAME, "config.json"),  # machine-wide (GPO/админ)
        os.path.join(base, VENDOR, APP_NAME, "config.json"),  # per-user (MSI-установка)
    ]
    if exe_dir:
        candidates.insert(0, os.path.join(exe_dir, "config.json"))  # рядом с EXE (portable)
    return [p for p in candidates if p and os.path.exists(p)]


def load_machine_config() -> dict:
    """
    Загружает и нормализует административный конфиг.
    Источники (по приоритету): реестр HKLM (GPO Preferences) → файлы config.json
    (ProgramData > LocalAppData > рядом с EXE) → переменная окружения (тесты).
    Ключи: jira_url, api_user, api_password, project_key, issue_type,
           faults (list), verify_ssl (bool), github_repo, github_token,
           update_enabled (bool)
    """
    raw = {}

    # 1) Реестр (GPO Preferences) — наивысший приоритет для администратора
    raw.update(_read_registry_config())

    # 2) Файлы config.json (ProgramData > LocalAppData > рядом с EXE)
    for p in _config_candidate_paths():
        try:
            raw.update(json.loads(Path(p).read_text(encoding="utf-8")))
        except Exception:
            pass

    # 3) Переменная окружения (для локальных тестов) — перекрывает всё
    env = os.environ.get("CSL_HELPDESK_CONFIG")
    if env:
        try:
            raw.update(json.loads(env))
        except Exception:
            pass

    # --- Нормализация ключей (допускаем разные имена) ---
    def get(*names, default=None):
        for n in names:
            if n in raw and raw[n] not in (None, ""):
                return raw[n]
        return default

    faults = get("faults", "fault_list", "faults_list")
    if isinstance(faults, str):
        faults = [f.strip() for f in faults.split(";") if f.strip()]
    if not faults:
        faults = list(DEFAULT_FAULTS)

    verify = get("verify_ssl", "verifyssl", default=True)
    if isinstance(verify, str):
        verify = verify.strip().lower() in ("1", "true", "yes", "on")

    update_enabled = get("update_enabled", "autoupdate", "auto_update", default=None)
    if isinstance(update_enabled, str):
        update_enabled = update_enabled.strip().lower() in ("1", "true", "yes", "on")
    # Если репозиторий не задан — автообновление недоступно в любом случае
    github_repo = get("github_repo", "githubrepo", "repo")
    if not github_repo:
        update_enabled = False
    elif update_enabled is None:
        update_enabled = True

    return {
        "jira_url": (get("jira_url", "baseurl", "base_url", "url") or "").rstrip("/"),
        "api_user": get("api_user", "username", "login", "user") or "",
        "api_password": get("api_password", "password", "pass") or "",
        "project_key": get("project_key", "project", "jira_project") or "",
        "issue_type": get("issue_type", "issuetype", default="Запрос") or "Запрос",
        "faults": faults,
        "verify_ssl": bool(verify),
        "github_repo": github_repo or "",
        "github_token": get("github_token", "token") or "",
        "update_enabled": bool(update_enabled),
    }


def load_user_data() -> dict:
    """Загружает пользовательские данные (телефон и т.п.)."""
    f = user_data_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_user_data(data: dict) -> None:
    """Сохраняет пользовательские данные (телефон и т.п.)."""
    f = user_data_file()
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_configured() -> bool:
    """Достаточно ли настроек для отправки заявки (есть URL и учётка)."""
    cfg = load_machine_config()
    return bool(cfg.get("jira_url") and cfg.get("api_user") and cfg.get("project_key"))
