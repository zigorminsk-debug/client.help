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
from pathlib import Path

VENDOR = "CSL"
APP_NAME = "HelpdeskClient"
APP_TITLE = "CSL — Заявка в техподдержку"

# Список неисправностей по умолчанию (используется, если не задан в админ-конфиге).
# Группировка — как в окне v3: заголовки групп + пункты в одну колонку.
DEFAULT_FAULT_GROUPS = [
    {"group": "Проблемы с ПК и порталами", "items": [
        "Не включается / не загружается",
        "Не работает интернет / сеть",
        "Медленно работает",
        "Не работает программа / ПО",
        "Проблема с электронной почтой",
        "Нужна установка / настройка ПО",
        "Прочее (описано в комментарии)",
    ]},
    {"group": "Проблемы с принтерами", "items": [
        "Не работает принтер",
        "Не работает сканер",
        "Не подключается принтер по сети",
        "Нужна заправка / ремонт принтера",
    ]},
    {"group": "Добавление и удаление пользователей", "items": [
        "Нужно добавить пользователя",
        "Нужно удалить пользователя",
        "Нужно изменить права доступа",
        "Восстановление учётной записи",
    ]},
]
DEFAULT_FAULTS = [item for g in DEFAULT_FAULT_GROUPS for item in g["items"]]


def _normalize_fault_groups(faults_raw, groups_raw):
    """Привести неисправности к виду (flat_list, groups_list).

    Поддерживаемые форматы админ-конфига:
      - fault_groups: [{"group": "...", "items": [...]}, ...]
      - fault_groups: {"Группа": [...], ...}
      - faults: [{"group": "...", "items": [...]}, ...]
      - faults: ["## Группа", "пункт", ...]  (строка с "## " — заголовок группы)
      - faults: ["пункт", ...]  (плоский список — одна группа без заголовка)
    Если ничего не задано — возвращаются DEFAULT_FAULT_GROUPS.
    """
    def _from_groups(raw):
        groups = []
        if isinstance(raw, dict):
            for name, items in raw.items():
                if isinstance(items, (list, tuple)):
                    groups.append({"group": str(name), "items": [str(x) for x in items if str(x).strip()]})
        elif isinstance(raw, (list, tuple)):
            for entry in raw:
                if isinstance(entry, dict):
                    name = entry.get("group") or entry.get("name") or entry.get("title") or ""
                    items = entry.get("items") or entry.get("faults") or entry.get("list") or []
                    if isinstance(items, str):
                        items = [x.strip() for x in items.split(";") if x.strip()]
                    groups.append({"group": str(name), "items": [str(x) for x in items if str(x).strip()]})
                elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                    groups.append({"group": str(entry[0]), "items": [str(x) for x in entry[1] if str(x).strip()]})
        groups = [g for g in groups if g["items"]]
        return groups or None

    groups = _from_groups(groups_raw)
    if groups is None and isinstance(faults_raw, (list, tuple)) and faults_raw and isinstance(faults_raw[0], dict):
        groups = _from_groups(faults_raw)
    if groups is None and isinstance(faults_raw, (list, tuple)):
        flat = []
        current = {"group": "", "items": []}
        grouped = []
        for entry in faults_raw:
            s = str(entry).strip()
            if not s:
                continue
            if s.startswith("## "):
                if current["items"]:
                    grouped.append(current)
                current = {"group": s[3:].strip(), "items": []}
            else:
                flat.append(s)
                current["items"].append(s)
        if current["items"]:
            grouped.append(current)
        if grouped and any(g["group"] for g in grouped):
            groups = grouped
        elif flat:
            groups = [{"group": "", "items": flat}]
    if not groups:
        groups = [{"group": g["group"], "items": list(g["items"])} for g in DEFAULT_FAULT_GROUPS]
    flat_list = [item for g in groups for item in g["items"]]
    return flat_list, groups


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


def load_machine_config() -> dict:
    """
    Загружает и нормализует административный конфиг.
    Ключи: jira_url, api_user, api_password, project_key, issue_type,
           faults (плоский list), fault_groups (list of {group, items}),
           verify_ssl (bool), github_repo, github_token,
           update_enabled (bool)
    """
    raw = {}

    # 1) Реестр (GPO Preferences)
    raw.update(_read_registry_config())

    # 2) Файл config.json в ProgramData (GPO-логон-скрипт)
    p = machine_config_path()
    if p.exists():
        try:
            raw.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass

    # 3) Переменная окружения (для локальных тестов)
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

    faults_raw = get("faults", "fault_list", "faults_list")
    if isinstance(faults_raw, str):
        faults_raw = [f.strip() for f in faults_raw.split(";") if f.strip()]
    groups_raw = get("fault_groups", "faultgroups", "fault_groups_list")
    faults, fault_groups = _normalize_fault_groups(faults_raw, groups_raw)

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
        "fault_groups": fault_groups,
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
