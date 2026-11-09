from __future__ import annotations

import asyncio

import pytest

from app.config import ProxyConfig
from app.proxy import http_proxy_for, is_internal_host


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.1.2.3",
        "172.20.0.5",
        "192.168.1.10",
        "169.254.1.1",
        "100.100.1.1",
        "::1",
        "fd00::1",
        "openlist",
        "nas.local",
        "files.lan",
        "dav.internal",
        "router.home.arpa",
    ],
)
def test_internal_hosts_bypass(host):
    assert is_internal_host(host)


@pytest.mark.parametrize("host", ["dav.example.com", "8.8.8.8", "2001:4860:4860::8888"])
def test_public_hosts_use_proxy(host):
    assert not is_internal_host(host)


def test_no_proxy_rules():
    assert is_internal_host("files.corp-example.com", ("corp-example.com",))
    assert is_internal_host("203.0.113.7", ("203.0.113.0/24",))
    assert not is_internal_host("example.org", ("corp-example.com",))


def test_proxy_from_env_and_urls():
    proxy = ProxyConfig.from_env({"PROXY_URL": "socks5://us%40r:p%3Ass@proxy.example:1081", "PROXY_APPLY_TO": "telegram"})
    assert proxy.enabled and proxy.username == "us@r" and proxy.password == "p:ss"
    assert proxy.apply_to_telegram and not proxy.apply_to_webdav
    assert proxy.httpx_url() == "socks5h://us%40r:p%3Ass@proxy.example:1081"
    assert proxy.telethon_proxy()["proxy_type"] == "socks5"
    assert "p:ss" not in proxy.describe()


def test_proxy_disabled_without_url():
    proxy = ProxyConfig.from_env({})
    assert not proxy.enabled and proxy.telethon_proxy() is None


def test_bad_proxy_scheme():
    with pytest.raises(ValueError):
        ProxyConfig.from_env({"PROXY_URL": "ftp://x:1"})


def test_http_proxy_decision():
    proxy = ProxyConfig.from_env({"PROXY_URL": "http://proxy.example:8080", "PROXY_BYPASS_RESOLVE": "false"})
    assert asyncio.run(http_proxy_for("http://openlist:5244/dav", proxy)) is None
    assert asyncio.run(http_proxy_for("http://192.168.1.2/dav", proxy)) is None
    assert asyncio.run(http_proxy_for("https://dav.example.com/dav", proxy)) == "http://proxy.example:8080"


def test_webdav_not_proxied_when_only_telegram():
    proxy = ProxyConfig.from_env({"PROXY_URL": "socks5://p:1", "PROXY_APPLY_TO": "telegram"})
    assert asyncio.run(http_proxy_for("https://dav.example.com", proxy)) is None
