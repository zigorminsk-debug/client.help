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
import re
from pathlib import Path

VENDOR = "CSL"
APP_NAME = "HelpdeskClient"
APP_TITLE = "CSL — Заявка в техподдержку"

# Неисправности разбиты на КАТЕТОРИИ (чекбоксы в GUI собираются по этим группам).
# Если в админ-конфиге задан плоский список — он распределится по категориям
# по ключевым словам (см. _normalize_fault_categories).
DEFAULT_FAULT_CATEGORIES = {
    "Проблемы с ПК и порталами": [
        "Не включается / не загружается",
        "Не работает интернет / сеть",
        "Медленно работает",
        "Не работает программа / ПО",
        "Проблема с электронной почтой",
        "Нужна установка / настройка ПО",
        "Прочее (описано в комментарии)",
    ],
    "Проблемы с принтерами": [
        "Не работает принтер",
        "Не работает сканер",
        "Не подключается принтер по сети",
        "Нужна заправка / ремонт принтера",
    ],
    "Добавление и удаление пользователей": [
        "Нужно добавить пользователя",
        "Нужно удалить пользователя",
        "Нужно изменить права доступа",
        "Восстановление учётной записи",
    ],
}

# Стандартный код страны для телефона (Беларусь). Можно переопределить
# в админ-конфиге ключом phone_country_code.
DEFAULT_PHONE_COUNTRY_CODE = "+375"


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
           faults (list), verify_ssl (bool), github_repo, github_token,
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

    fault_categories = _normalize_fault_categories(
        get("fault_categories", "fault_cats", "faults")
    )
    phone_cc = get("phone_country_code", "phone_cc", default=DEFAULT_PHONE_COUNTRY_CODE)
    if not isinstance(phone_cc, str):
        phone_cc = DEFAULT_PHONE_COUNTRY_CODE

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

    # Плоский список неисправностей (для backward-compatibility) — из категорий.
    faults = [label for group in fault_categories.values() for label in group]

    return {
        "jira_url": (get("jira_url", "baseurl", "base_url", "url") or "").rstrip("/"),
        "api_user": get("api_user", "username", "login", "user") or "",
        "api_password": get("api_password", "password", "pass") or "",
        "project_key": get("project_key", "project", "jira_project") or "",
        "issue_type": get("issue_type", "issuetype", default="Запрос") or "Запрос",
        "faults": faults,
        "fault_categories": fault_categories,
        "phone_country_code": phone_cc,
        "verify_ssl": bool(verify),
        "github_repo": github_repo or "",
        "github_token": get("github_token", "token") or "",
        "update_enabled": bool(update_enabled),
    }


def _normalize_fault_categories(raw) -> dict:
    """Привожит конфиг неисправностей к словарю {категория: [label, ...]}.

    Поддерживает оба варианта:
      * словарь {"категория": ["...", ...]};
      * плоский список — распределяет по категориям по ключевым словам.
    """
    if isinstance(raw, dict) and raw:
        out = {}
        for group, items in raw.items():
            if isinstance(items, str):
                items = [f.strip() for f in items.split(";") if f.strip()]
            if isinstance(items, list):
                out[str(group)] = [str(i).strip() for i in items if str(i).strip()]
        if out:
            return out
        return {k: list(v) for k, v in DEFAULT_FAULT_CATEGORIES.items()}

    # Плоский список — распределяем по категориям по ключевым словам.
    if isinstance(raw, str):
        raw = [f.strip() for f in raw.split(";") if f.strip()]
    if isinstance(raw, list) and raw:
        buckets = {k: [] for k in DEFAULT_FAULT_CATEGORIES}
        printers_kw = ("принтер", "сканер", "заправ")
        users_kw = ("пользоват", "учёт", "доступ", "права")
        for item in raw:
            item = str(item).strip()
            low = item.lower()
            if any(k in low for k in printers_kw):
                buckets["Проблемы с принтерами"].append(item)
            elif any(k in low for k in users_kw):
                buckets["Добавление и удаление пользователей"].append(item)
            else:
                buckets["Проблемы с ПК и порталами"].append(item)
        out = {k: v for k, v in buckets.items() if v}
        return out or {k: list(v) for k, v in DEFAULT_FAULT_CATEGORIES.items()}

    return {k: list(v) for k, v in DEFAULT_FAULT_CATEGORIES.items()}


# --------------------------------------------------------------------------
# Телефон — международный формат
# --------------------------------------------------------------------------
def normalize_phone(raw: str, country_code: str = DEFAULT_PHONE_COUNTRY_CODE) -> str:
    """Привожит телефон к международному формату: '+CC NNN NNN NNN'.

    - убирает все разделители/знаки (оставляет только цифры);
    - убирает национальный лидирующий 0 и уже указанный код страны;
    - если цифр не осталось — возвращает пустую строку.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return ""
    cc = re.sub(r"\D", "", country_code or "")
    if cc and digits.startswith(cc):
        digits = digits[len(cc):]
    if digits.startswith("0"):
        digits = digits[1:]
    if not digits:
        return ""
    # Группируем по 3 цифры: '+375 291 234 567'.
    groups = " ".join(digits[i:i + 3] for i in range(0, len(digits), 3))
    return f"+{cc} {groups}".strip()


def is_valid_phone(normalized: str) -> bool:
    """Минимальная проверка: непустой и достаточной длины (>=8 цифр)."""
    if not normalized:
        return False
    digits = re.sub(r"\D", "", normalized)
    return len(digits) >= 8


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
