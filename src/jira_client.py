"""
jira_client.py — клиент REST API Jira (Server / Data Center) для создания заявок.

Аутентификация: Basic (единый логин/пароль организации).
Сначала создаётся задача через /rest/api/2/issue, затем к ней прикрепляются
файлы (скриншот, голосовое сообщение) через /rest/api/2/issue/{key}/attachments.

Примечание: h.csl.by — это Jira Server (судя по Dashboard.jspa), поэтому
Basic-аутентификация по логину и паролю работает напрямую.
"""

import mimetypes
import os

import requests


class JiraError(Exception):
    pass


class JiraClient:
    def __init__(self, base_url: str, username: str, password: str,
                 project_key: str, issue_type: str = "Запрос",
                 verify_ssl: bool = True, timeout: int = 60):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.project_key = project_key
        self.issue_type = issue_type
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # --- проверка доступности и учётных данных ---
    def test_connection(self) -> dict:
        """Возвращает информацию о текущем пользователе или бросает JiraError."""
        url = f"{self.base_url}/rest/api/2/myself"
        try:
            r = self.session.get(url, timeout=self.timeout, verify=self.verify_ssl)
        except requests.RequestException as e:
            raise JiraError(f"Нет связи с {self.base_url}: {e}")
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403):
            raise JiraError("Ошибка авторизации: неверный логин или пароль (проверьте настройки GPO).")
        raise JiraError(f"Сервер вернул {r.status_code}: {r.text[:300]}")

    # --- создание заявки ---
    def create_issue(self, summary: str, description: str) -> str:
        url = f"{self.base_url}/rest/api/2/issue"
        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": summary,
                "description": description,
                "issuetype": {"name": self.issue_type},
            }
        }
        r = self.session.post(url, json=payload, timeout=self.timeout, verify=self.verify_ssl)
        if r.status_code in (201, 200):
            return r.json().get("key")
        # Попытка извлечь человекочитаемую ошибку
        msg = r.text[:500]
        try:
            errs = r.json().get("errors", {})
            if errs:
                msg = "; ".join(f"{k}: {v}" for k, v in errs.items())
        except Exception:
            pass
        raise JiraError(f"Не удалось создать заявку ({r.status_code}): {msg}")

    # --- вложения ---
    def attach_file(self, issue_key: str, file_path: str) -> bool:
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}/attachments"
        self.session.headers.pop("Content-Type", None)  # multipart сам задаёт boundary
        try:
            with open(file_path, "rb") as f:
                r = self.session.post(
                    url,
                    files={"file": (os.path.basename(file_path), f,
                                    mimetypes.guess_type(file_path)[0] or "application/octet-stream")},
                    headers={"X-Atlassian-Token": "no-check"},
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )
        finally:
            self.session.headers["Content-Type"] = "application/json"
        if r.status_code in (201, 200):
            return True
        raise JiraError(f"Не удалось прикрепить файл {os.path.basename(file_path)} "
                        f"({r.status_code}): {r.text[:200]}")

    def submit(self, summary: str, description: str, attachments: list = None) -> dict:
        """Создаёт заявку и прикрепляет файлы. Возвращает {key, attachments}."""
        key = self.create_issue(summary, description)
        attached = []
        for path in (attachments or []):
            if path and os.path.exists(path):
                try:
                    self.attach_file(key, path)
                    attached.append(os.path.basename(path))
                except JiraError as e:
                    # Вложение не критично — заявка уже создана
                    attached.append(f"{os.path.basename(path)} (ошибка: {e})")
        return {"key": key, "attachments": attached}
