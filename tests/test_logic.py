"""
Самопроверка логики клиента (без GUI и без реальной Jira/GitHub).
Запуск:  python -m pytest tests/   или   python tests/test_logic.py
"""

import os
import sys
import json
import threading
import http.server

import pytest

# Делаем видимыми модули из src/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import config as cfg
import system_info as sysinfo
import jira_client as jira
import updater as updater


# --------------------------------------------------------------------- config
def test_machine_config_from_env():
    payload = json.dumps({
        "jira_url": "https://example.test",
        "api_user": "svc",
        "api_password": "secret",
        "project_key": "HELP",
        "github_repo": "owner/repo",
        "update_enabled": True,
    })
    os.environ["CSL_HELPDESK_CONFIG"] = payload
    try:
        c = cfg.load_machine_config()
        assert c["jira_url"] == "https://example.test"
        assert c["api_user"] == "svc"
        assert c["project_key"] == "HELP"
        assert c["github_repo"] == "owner/repo"
        assert c["update_enabled"] is True
        assert c["faults"]  # дефолтный список есть
    finally:
        del os.environ["CSL_HELPDESK_CONFIG"]


def test_update_disabled_without_repo():
    payload = json.dumps({"jira_url": "x", "api_user": "u", "api_password": "p",
                          "project_key": "K", "update_enabled": True})
    os.environ["CSL_HELPDESK_CONFIG"] = payload
    try:
        c = cfg.load_machine_config()
        assert c["update_enabled"] is False  # нет github_repo -> отключено
    finally:
        del os.environ["CSL_HELPDESK_CONFIG"]


def test_user_data_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cfg.save_user_data({"phone": "+375291112233"})
    d = cfg.load_user_data()
    assert d["phone"] == "+375291112233"


# ----------------------------------------------------------------- system info
def test_system_info_collects():
    info = sysinfo.get_system_info()
    assert info["hostname"]
    assert "cpu" in info and "ram" in info and "disks" in info
    markup = sysinfo.format_system_info_markup(info)
    assert "Сведения о системе" in markup


# ------------------------------------------------------------------- jira mock
class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth_ok(self):
        return self.headers.get("Authorization", "").startswith("Basic ")

    def do_GET(self):
        if self.path == "/rest/api/2/myself":
            if not self._auth_ok():
                self.send_response(401); self.end_headers(); return
            import base64
            try:
                enc = self.headers.get("Authorization", "").split(" ", 1)[1]
                decoded = base64.b64decode(enc).decode("utf-8", "ignore")
                user, _, pwd = decoded.partition(":")
                if user != "svc" or pwd != "secret":
                    self.send_response(401); self.end_headers(); return
            except Exception:
                self.send_response(401); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"name": "svc"}).encode())

    def do_POST(self):
        if self.path == "/rest/api/2/issue":
            if not self._auth_ok():
                self.send_response(401); self.end_headers(); return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            assert body["fields"]["project"]["key"] == "HELP"
            assert body["fields"]["summary"]
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"key": "HELP-123", "id": 1}).encode())
        elif self.path.endswith("/attachments"):
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"filename": "x.png"}]).encode())


@pytest.fixture
def mock_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_jira_submit(mock_server):
    client = jira.JiraClient(mock_server, "svc", "secret", "HELP", verify_ssl=False)
    me = client.test_connection()
    assert me["name"] == "svc"
    res = client.submit("Тест", "Описание", attachments=[])
    assert res["key"] == "HELP-123"


def test_jira_authentication_error(mock_server):
    bad = jira.JiraClient(mock_server, "svc", "wrong", "HELP", verify_ssl=False)
    with pytest.raises(jira.JiraError):
        bad.test_connection()


# ------------------------------------------------------------------- updater
def test_version_parsing():
    assert updater.parse_version("v12") == 12
    assert updater.parse_version("1.0.12") == 12
    assert updater.parse_version("release-7") == 7
    assert updater.is_newer("v13", "12") is True
    assert updater.is_newer("v12", "12") is False
    assert updater.is_newer("v11", "12") is False


class _ReleaseHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/releases/latest"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "tag_name": "v5",
                "assets": [{"name": "CSLHelpdesk.exe",
                            "browser_download_url": "http://x/y.exe"}],
            }).encode())


@pytest.fixture
def release_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _ReleaseHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_updater_get_latest(release_server):
    rel = updater.get_latest_release("owner/repo", base_url=release_server)
    assert rel["tag_name"] == "v5"
    asset = updater.find_exe_asset(rel)
    assert asset["browser_download_url"] == "http://x/y.exe"
    assert updater.is_newer(rel["tag_name"], "3") is True
    assert updater.is_newer(rel["tag_name"], "5") is False


def test_normalize_phone_international():
    assert cfg.normalize_phone("29 123 45 67") == "+375 291 234 567"
    assert cfg.normalize_phone("+375291234567") == "+375 291 234 567"
    assert cfg.normalize_phone("375291234567") == "+375 291 234 567"
    assert cfg.normalize_phone("0291234567") == "+375 291 234 567"
    assert cfg.normalize_phone("") == ""
    assert cfg.normalize_phone("abc") == ""
    assert cfg.is_valid_phone("+375 291 234 567") is True
    assert cfg.is_valid_phone("") is False
    assert cfg.is_valid_phone("123") is False


def test_fault_categories_split(monkeypatch):
    # По умолчанию — три категории.
    cats = cfg._normalize_fault_categories(None)
    assert set(cats) == {"Проблемы с ПК и порталами", "Проблемы с принтерами",
                         "Добавление и удаление пользователей"}
    # Плоский список распределяется по категориям по ключевым словам.
    cats2 = cfg._normalize_fault_categories(
        ["Не работает принтер", "Нужно добавить пользователя", "Медленно работает"])
    assert "Не работает принтер" in cats2["Проблемы с принтерами"]
    assert "Нужно добавить пользователя" in cats2["Добавление и удаление пользователей"]
    assert "Медленно работает" in cats2["Проблемы с ПК и порталами"]
    # Словарь-категории проходят через нормализацию как есть.
    cats3 = cfg._normalize_fault_categories({"X": ["a"], "Y": ["b"]})
    assert cats3 == {"X": ["a"], "Y": ["b"]}


def test_machine_config_has_fault_categories(monkeypatch):
    payload = json.dumps({
        "jira_url": "https://example.test",
        "api_user": "svc",
        "api_password": "secret",
        "project_key": "HELP",
    })
    monkeypatch.setenv("CSL_HELPDESK_CONFIG", payload)
    c = cfg.load_machine_config()
    assert set(c["fault_categories"]) == {"Проблемы с ПК и порталами",
                                          "Проблемы с принтерами",
                                          "Добавление и удаление пользователей"}
    assert c["phone_country_code"] == "+375"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
