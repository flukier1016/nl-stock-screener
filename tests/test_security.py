"""安全：响应头、不公开接口文档、管理口令、AI 限流、前端不用内联脚本（配合内容安全策略）。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mingshai import app as appmod
from mingshai import llm, parse_llm

STATIC = Path(appmod.__file__).parent / "static"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MINGSHAI_AUTO_BUILD", "0")
    monkeypatch.setattr(appmod.monitors, "DB_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(llm, "available", lambda: False)
    return TestClient(appmod.app)


def test_security_headers_on_page_and_api(client):
    page = client.get("/")
    assert page.status_code == 200
    for k in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
              "Strict-Transport-Security", "Permissions-Policy"):
        assert k in page.headers, k
    assert "script-src 'self';" in page.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    api = client.get("/api/health")
    assert api.headers["Cache-Control"] == "no-store" and api.headers["X-Content-Type-Options"] == "nosniff"


def test_api_docs_are_not_public(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_browser_receives_fingerprinted_assets_and_revalidates_old_urls(client):
    page = client.get("/")
    assert page.headers["Cache-Control"] == "no-store"
    assert f"/static/app.js?v={appmod.ASSET_VERSION}" in page.text
    assert f"/static/style.css?v={appmod.ASSET_VERSION}" in page.text
    asset = client.get("/static/app.js")
    assert "must-revalidate" in asset.headers["Cache-Control"]


def test_admin_endpoints_need_the_exact_token(client, monkeypatch):
    monkeypatch.setenv("MINGSHAI_ADMIN_TOKEN", "right-token")
    for auth in (None, "Bearer wrong-token", "right-token", "Bearer right-token-x"):
        headers = {"Authorization": auth} if auth else {}
        assert client.post("/api/admin/build", headers=headers).status_code == 401
    monkeypatch.delenv("MINGSHAI_ADMIN_TOKEN")
    assert client.post("/api/admin/build", headers={"Authorization": "Bearer "}).status_code == 401  # 没配口令就一律拒绝


def test_ai_gate_limits_per_origin_and_in_total():
    g = appmod.AIGate(per_client=2, client_window=600, total=3, total_window=3600)
    assert g.take("a") is None and g.take("a") is None
    assert "同一来源" in g.take("a")           # 同一来源超限
    assert g.take("b") is None                 # 别的来源还能用
    assert "全站" in g.take("c")               # 全站超限


def test_parse_uses_rules_when_gate_says_no(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)

    def must_not_call(*a, **k):
        raise AssertionError("限流时不应调用大模型")
    monkeypatch.setattr(llm, "complete_json", must_not_call)
    r = parse_llm.parse("经营改善、估值合理", {}, gate=lambda: "AI 调用太频繁")
    assert r.mode == "rules" and r.fallback_reason.startswith("AI 调用太频繁")


def test_frontend_has_no_inline_scripts_or_handlers():
    # 内容安全策略只允许本站脚本文件；内联 <script> 和 onclick= 之类会被浏览器拦掉
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    for text in (html, js):
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text)


def test_repo_has_no_secrets_or_personal_paths():
    root = Path(appmod.__file__).parents[1]
    tracked = [p for p in root.rglob("*") if p.is_file() and not any(
        part in {".git", ".venv", "data", "build", "__pycache__", ".pytest_cache", ".ruff_cache"} for part in p.relative_to(root).parts)
        and p.name != ".env" and p.suffix not in {".duckdb", ".sqlite", ".mp4", ".jpg", ".png", ".zip"}]
    pat = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|/Users/[a-z]|-----BEGIN [A-Z ]*PRIVATE KEY-----")
    hits = [str(p.relative_to(root)) for p in tracked if pat.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []
