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


def test_anydesk_installed_cli_and_config(tmp_path, monkeypatch):
    from types import SimpleNamespace
    program = tmp_path / "program"
    exe = program / "AnyDesk" / "AnyDesk.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setenv("ProgramFiles", str(program))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="123 456 789\n")
    monkeypatch.setattr(sysinfo.subprocess, "run", run)
    assert sysinfo.get_anydesk_id() == "123456789"
    assert calls[0] == [str(exe), "--get-id"]
    monkeypatch.setattr(sysinfo, "_cli_id", lambda *a, **k: "")
    config = tmp_path / "data" / "AnyDesk" / "service.conf"
    config.parent.mkdir(parents=True)
    config.write_text('other=123\nad.anynet.id=987654321\n', encoding="utf-8")
    monkeypatch.setenv("ProgramData", str(tmp_path / "data"))
    assert sysinfo.get_anydesk_id() == "987654321"


def test_rustdesk_service_config_and_cli_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "app"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "data"))
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    path = tmp_path / "Windows" / "System32" / "config" / "systemprofile" / "AppData" / "Roaming" / "RustDesk" / "config" / "RustDesk.toml"
    path.parent.mkdir(parents=True)
    path.write_text('identity = "wrong"\nid = "123456789"\n', encoding="utf-8")
    monkeypatch.setattr(sysinfo, "_cli_id", lambda *a, **k: "fallback123")
    assert sysinfo.get_rustdesk_id() == "123456789"
    path.unlink()
    assert sysinfo.get_rustdesk_id() == "fallback123"


def test_cli_rejects_messages_and_failed_results(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(sysinfo, "_installed_executables", lambda name: ["one", "two", "three"])
    results = iter([SimpleNamespace(returncode=0, stdout="error: no id"),
                    SimpleNamespace(returncode=1, stdout="123"),
                    SimpleNamespace(returncode=0, stdout="456\n")])
    monkeypatch.setattr(sysinfo.subprocess, "run", lambda *a, **k: next(results))
    assert sysinfo._cli_id("AnyDesk", numeric=True) == "456"


def test_phone_normalization():
    assert cfg.normalize_phone("293371412") == "+375 29 337-14-12"
    assert cfg.normalize_phone("+1 202 555 0123").startswith("+1")
    with pytest.raises(ValueError):
        cfg.normalize_phone("123")


def test_bundled_update_repo_and_explicit_disable(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "machine_config_path", lambda: tmp_path / "missing")
    monkeypatch.setattr(cfg, "_read_registry_config", lambda: {})
    monkeypatch.setattr(cfg._version, "GITHUB_REPO", "owner/repo")
    monkeypatch.delenv("CSL_HELPDESK_CONFIG", raising=False)
    assert cfg.load_machine_config()["update_enabled"] is True
    monkeypatch.setenv("CSL_HELPDESK_CONFIG", '{"update_enabled": false}')
    assert cfg.load_machine_config()["github_repo"] == "owner/repo"
    assert cfg.load_machine_config()["update_enabled"] is False


def test_audio_checks_input_channels(monkeypatch):
    import audio_recorder as audio
    from types import SimpleNamespace
    monkeypatch.setattr(audio, "_SD_AVAILABLE", True)
    monkeypatch.setattr(audio, "sd", SimpleNamespace(query_devices=lambda: [{"max_input_channels": 0}]))
    assert not audio.is_available()
    monkeypatch.setattr(audio.sd, "query_devices", lambda: [{"max_input_channels": 1}])
    assert audio.is_available()


def test_screenshot_selection_scaling():
    import screenshot
    from PIL import Image
    image = Image.new("RGB", (200, 100))
    assert screenshot.crop_selection(image, (75, 40), (25, 10), (100, 50)).size == (100, 60)
    assert screenshot.crop_selection(image, (2, 2), (2, 10), (100, 50)) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
