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


# ------------------------------------------------------- remote IDs (AnyDesk/RustDesk)
def test_validate_remote_id():
    assert sysinfo.validate_remote_id("123456789") == "123456789"
    assert sysinfo.validate_remote_id("'123456789'") == "123456789"
    assert sysinfo.validate_remote_id('"123456789"') == "123456789"
    assert sysinfo.validate_remote_id("  123456789\n") == "123456789"
    assert sysinfo.validate_remote_id("") == ""
    assert sysinfo.validate_remote_id(None) == ""
    assert sysinfo.validate_remote_id("abc") == ""
    assert sysinfo.validate_remote_id("123") == ""  # слишком короткий


def test_parse_anydesk_conf():
    assert sysinfo.parse_anydesk_conf("ad_anywhere_id = 123456789\n") == "123456789"
    assert sysinfo.parse_anydesk_conf("ad_anywhere_id=987654321\n") == "987654321"
    assert sysinfo.parse_anydesk_conf("other = 1\n") == ""
    assert sysinfo.parse_anydesk_conf("") == ""


def test_parse_rustdesk_toml_single_quotes():
    # RustDesk пишет id в одинарных кавычках — старый парсер их не снимал.
    assert sysinfo.parse_rustdesk_toml("id = '123456789'\n") == "123456789"
    assert sysinfo.parse_rustdesk_toml('id = "123456789"\n') == "123456789"
    assert sysinfo.parse_rustdesk_toml("id = 123456789\n") == "123456789"
    assert sysinfo.parse_rustdesk_toml("id='123456789'\n") == "123456789"
    assert sysinfo.parse_rustdesk_toml("id = ''\n") == ""
    assert sysinfo.parse_rustdesk_toml("password = 'x'\n") == ""


def test_extract_cli_id():
    assert sysinfo.extract_cli_id("123456789\n") == "123456789"
    assert sysinfo.extract_cli_id("  123456789  \n") == "123456789"
    assert sysinfo.extract_cli_id("") == ""
    assert sysinfo.extract_cli_id("error") == ""


def test_rustdesk_id_from_toml_file(tmp_path, monkeypatch):
    toml = tmp_path / "RustDesk.toml"
    toml.write_text("id = '555123456'\n[options]\n", encoding="utf-8")
    monkeypatch.setattr(sysinfo, "_candidate_rustdesk_exes", lambda: [])
    monkeypatch.setattr(sysinfo, "_candidate_rustdesk_confs", lambda exes=None: [str(toml)])
    assert sysinfo.get_rustdesk_id() == "555123456"
    vid, src = sysinfo.get_rustdesk_id_with_source()
    assert vid == "555123456" and "RustDesk.toml" in src


def test_anydesk_id_from_conf_file(tmp_path, monkeypatch):
    conf = tmp_path / "anydesk.conf"
    conf.write_text("ad_anywhere_id = 777888999\n", encoding="utf-8")
    monkeypatch.setattr(sysinfo, "_candidate_anydesk_exes", lambda: [])
    monkeypatch.setattr(sysinfo, "_candidate_anydesk_confs", lambda exes=None: [str(conf)])
    assert sysinfo.get_anydesk_id() == "777888999"


def test_detect_remote_ids_shape(monkeypatch):
    monkeypatch.setattr(sysinfo, "_candidate_anydesk_exes", lambda: [])
    monkeypatch.setattr(sysinfo, "_candidate_anydesk_confs", lambda exes=None: [])
    monkeypatch.setattr(sysinfo, "_candidate_rustdesk_exes", lambda: [])
    monkeypatch.setattr(sysinfo, "_candidate_rustdesk_confs", lambda exes=None: [])
    res = sysinfo.detect_remote_ids()
    assert set(res) == {"anydesk", "rustdesk"}
    assert res["anydesk"]["id"] == "" and res["rustdesk"]["id"] == ""


# --------------------------------------------------------------- fault groups
def test_fault_groups_default():
    flat, groups = cfg._normalize_fault_groups(None, None)
    assert flat and groups
    assert flat == [i for g in groups for i in g["items"]]
    assert any(g["group"] for g in groups)  # есть заголовки групп


def test_fault_groups_flat_list():
    flat, groups = cfg._normalize_fault_groups(["a", "b"], None)
    assert flat == ["a", "b"]
    assert groups == [{"group": "", "items": ["a", "b"]}]


def test_fault_groups_from_dicts():
    raw = [{"group": "Принтеры", "items": ["x", "y"]}, {"group": "Сеть", "items": ["z"]}]
    flat, groups = cfg._normalize_fault_groups(raw, None)
    assert flat == ["x", "y", "z"]
    assert groups[0]["group"] == "Принтеры"


def test_fault_groups_from_hash_headers():
    flat, groups = cfg._normalize_fault_groups(["## Сеть", "a", "## ПК", "b"], None)
    assert flat == ["a", "b"]
    assert [g["group"] for g in groups] == ["Сеть", "ПК"]


def test_machine_config_includes_fault_groups():
    payload = json.dumps({"jira_url": "x", "api_user": "u", "api_password": "p",
                          "project_key": "K", "fault_groups": [{"group": "G", "items": ["i1"]}]})
    os.environ["CSL_HELPDESK_CONFIG"] = payload
    try:
        c = cfg.load_machine_config()
        assert c["fault_groups"] == [{"group": "G", "items": ["i1"]}]
        assert c["faults"] == ["i1"]
    finally:
        del os.environ["CSL_HELPDESK_CONFIG"]


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
