"""Unit tests for download authentication.

Covers env-key resolution, OAuth token resolution/refresh/expiry, provider
host matching (including the per-hop drop on a CDN host), and the auth routes.
Async tests are driven via ``asyncio.run`` so no pytest-asyncio plugin is needed.
"""

from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp.test_utils import make_mocked_request

from app.model_downloader.api import routes
from app.model_downloader.auth import oauth, token_store
from app.model_downloader.auth.providers import PROVIDERS, provider_for_host
from app.model_downloader.auth.resolver import resolve_auth_for_hop
from app.model_downloader.auth.store import AUTH_STORE
from app.model_downloader.auth.token_store import Token

_HF_ENV = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
_CIVITAI_ENV = ("CIVITAI_API_TOKEN", "CIVITAI_API_KEY")


@pytest.fixture
def auth_tmp(monkeypatch, tmp_path):
    """Isolate the on-disk token store and clear the in-memory cache."""
    d = tmp_path / "download_auth"
    d.mkdir()
    monkeypatch.setattr(token_store, "_auth_dir", lambda: str(d))
    AUTH_STORE._cache.clear()
    yield
    AUTH_STORE._cache.clear()


def _clear_env(monkeypatch, *names):
    for name in names:
        monkeypatch.delenv(name, raising=False)


# ----- provider host matching -----


def test_provider_for_host():
    assert provider_for_host("HuggingFace.co:443").name == "huggingface"
    assert provider_for_host("civitai.com").name == "civitai"
    # sibling CDN hosts must not match — this is what drops the token on redirect
    assert provider_for_host("cdn-lfs.huggingface.co") is None
    assert provider_for_host("cas-bridge.xethub.hf.co") is None
    assert provider_for_host("example.com") is None


# ----- env-key resolution -----


def test_env_key_resolution_hf(monkeypatch, auth_tmp):
    monkeypatch.setenv("HF_TOKEN", "hf_env")

    async def _run():
        auth = await resolve_auth_for_hop("huggingface.co", "https")
        assert auth is not None
        assert auth.headers["Authorization"] == "Bearer hf_env"
        # never over http, never on a CDN redirect host
        assert await resolve_auth_for_hop("huggingface.co", "http") is None
        assert await resolve_auth_for_hop("cdn-lfs.huggingface.co", "https") is None

    asyncio.run(_run())


def test_env_key_resolution_civitai_secondary_var(monkeypatch, auth_tmp):
    _clear_env(monkeypatch, "CIVITAI_API_TOKEN")
    monkeypatch.setenv("CIVITAI_API_KEY", "civ_env")

    async def _run():
        auth = await resolve_auth_for_hop("civitai.com", "https")
        assert auth is not None
        assert auth.headers["Authorization"] == "Bearer civ_env"

    asyncio.run(_run())


def test_env_key_takes_precedence_over_oauth(monkeypatch, auth_tmp):
    monkeypatch.setenv("HF_TOKEN", "hf_env")

    async def _run():
        AUTH_STORE.set_token("huggingface", Token(access_token="oauth_acc"))
        auth = await resolve_auth_for_hop("huggingface.co", "https")
        assert auth.headers["Authorization"] == "Bearer hf_env"

    asyncio.run(_run())


# ----- OAuth token resolution / refresh / expiry -----


def test_oauth_token_resolution(monkeypatch, auth_tmp):
    _clear_env(monkeypatch, *_HF_ENV)

    async def _run():
        AUTH_STORE.set_token("huggingface", Token(access_token="acc", expires_at=0))
        auth = await resolve_auth_for_hop("huggingface.co", "https")
        assert auth is not None
        assert auth.headers["Authorization"] == "Bearer acc"

    asyncio.run(_run())


def test_oauth_refresh_on_expiry(monkeypatch, auth_tmp):
    _clear_env(monkeypatch, *_HF_ENV)

    async def fake_refresh(provider, tok):
        return Token(
            access_token="new_acc",
            refresh_token="r2",
            expires_at=int(time.time()) + 3600,
        )

    monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)

    async def _run():
        AUTH_STORE.set_token(
            "huggingface",
            Token(access_token="old", refresh_token="r1", expires_at=1),
        )
        access = await AUTH_STORE.get_valid_token(PROVIDERS["huggingface"])
        assert access == "new_acc"
        # the refreshed token is persisted (cache + disk)
        assert token_store.load("huggingface").access_token == "new_acc"

    asyncio.run(_run())


def test_oauth_expired_without_refresh_returns_none(monkeypatch, auth_tmp):
    _clear_env(monkeypatch, *_HF_ENV)

    async def _run():
        AUTH_STORE.set_token(
            "huggingface",
            Token(access_token="old", refresh_token=None, expires_at=1),
        )
        assert await AUTH_STORE.get_valid_token(PROVIDERS["huggingface"]) is None
        assert await resolve_auth_for_hop("huggingface.co", "https") is None

    asyncio.run(_run())


def test_no_auth_when_nothing_configured(monkeypatch, auth_tmp):
    _clear_env(monkeypatch, *_HF_ENV, *_CIVITAI_ENV)

    async def _run():
        assert await resolve_auth_for_hop("huggingface.co", "https") is None
        assert await resolve_auth_for_hop("example.com", "https") is None

    asyncio.run(_run())


# ----- auth routes -----


def test_auth_status_route(monkeypatch, auth_tmp):
    _clear_env(monkeypatch, *_HF_ENV, *_CIVITAI_ENV)

    async def _run():
        resp = await routes.auth_status(make_mocked_request("GET", "/api/download/auth"))
        data = json.loads(resp.body)
        by_name = {p["provider"]: p for p in data["providers"]}
        assert set(by_name) == {"huggingface", "civitai"}
        assert by_name["huggingface"]["logged_in"] is False
        assert by_name["huggingface"]["env_key_present"] is False

    asyncio.run(_run())


def test_login_unconfigured_returns_400(monkeypatch, auth_tmp):
    _clear_env(monkeypatch, "COMFY_HF_OAUTH_CLIENT_ID")

    async def _run():
        req = make_mocked_request(
            "POST", "/api/download/auth/huggingface/login",
            match_info={"provider": "huggingface"},
        )
        resp = await routes.auth_login(req)
        assert resp.status == 400
        assert json.loads(resp.body)["error"]["code"] == "OAUTH_NOT_CONFIGURED"

    asyncio.run(_run())


def test_login_unknown_provider_returns_400(auth_tmp):
    async def _run():
        req = make_mocked_request(
            "POST", "/api/download/auth/nope/login",
            match_info={"provider": "nope"},
        )
        resp = await routes.auth_login(req)
        assert resp.status == 400
        assert json.loads(resp.body)["error"]["code"] == "UNKNOWN_PROVIDER"

    asyncio.run(_run())


def test_login_start_and_in_progress(monkeypatch, auth_tmp):
    monkeypatch.setenv("COMFY_HF_OAUTH_CLIENT_ID", "test-client")

    async def _run():
        try:
            req = make_mocked_request(
                "POST", "/api/download/auth/huggingface/login",
                match_info={"provider": "huggingface"},
            )
            resp = await routes.auth_login(req)
            assert resp.status == 200
            url = json.loads(resp.body)["authorize_url"]
            assert url.startswith("https://huggingface.co/oauth/authorize?")
            assert "code_challenge=" in url and "code_challenge_method=S256" in url
            assert "client_id=test-client" in url
            # a second concurrent login is rejected
            resp2 = await routes.auth_login(
                make_mocked_request(
                    "POST", "/api/download/auth/huggingface/login",
                    match_info={"provider": "huggingface"},
                )
            )
            assert resp2.status == 409
        finally:
            flow = oauth._ACTIVE.get("huggingface")
            if flow is not None:
                await flow._teardown()

    asyncio.run(_run())


def _redirect_port(authorize_url: str) -> int:
    redirect = parse_qs(urlsplit(authorize_url).query)["redirect_uri"][0]
    return urlsplit(redirect).port


def test_concurrent_logins_different_providers(monkeypatch, auth_tmp):
    """Two providers can log in at once — each binds its own loopback port."""
    monkeypatch.setenv("COMFY_HF_OAUTH_CLIENT_ID", "hf-client")
    monkeypatch.setenv("COMFY_CIVITAI_OAUTH_CLIENT_ID", "civ-client")

    async def _run():
        try:
            hf = await routes.auth_login(
                make_mocked_request(
                    "POST", "/api/download/auth/huggingface/login",
                    match_info={"provider": "huggingface"},
                )
            )
            civ = await routes.auth_login(
                make_mocked_request(
                    "POST", "/api/download/auth/civitai/login",
                    match_info={"provider": "civitai"},
                )
            )
            assert hf.status == 200 and civ.status == 200
            hf_port = _redirect_port(json.loads(hf.body)["authorize_url"])
            civ_port = _redirect_port(json.loads(civ.body)["authorize_url"])
            # distinct, OS-assigned loopback ports (no fixed-port collision)
            assert hf_port and civ_port and hf_port != civ_port
        finally:
            for name in ("huggingface", "civitai"):
                flow = oauth._ACTIVE.get(name)
                if flow is not None:
                    await flow._teardown()

    asyncio.run(_run())


def test_logout_route_clears_token(auth_tmp):
    async def _run():
        AUTH_STORE.set_token("civitai", Token(access_token="x"))
        assert AUTH_STORE.status(PROVIDERS["civitai"])["logged_in"] is True
        req = make_mocked_request(
            "POST", "/api/download/auth/civitai/logout",
            match_info={"provider": "civitai"},
        )
        resp = await routes.auth_logout(req)
        assert resp.status == 200
        assert json.loads(resp.body)["logged_out"] is True
        assert AUTH_STORE.status(PROVIDERS["civitai"])["logged_in"] is False

    asyncio.run(_run())
