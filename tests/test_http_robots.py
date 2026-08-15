from __future__ import annotations

import httpx
import pytest

from news_topic_monitor.http import (
    HttpRequestError,
    RobotsDeniedError,
    RobotsUnavailableError,
    SafeHttpClient,
)
from news_topic_monitor.settings import ContactRequiredError, Settings, validate_contact


def settings(tmp_path, *, retries: int = 0) -> Settings:
    return Settings(
        root=tmp_path,
        contact="monitor@example.org",
        request_interval_seconds=0,
        max_retries=retries,
    )


def test_robots_allows_and_blocks_before_request(tmp_path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="ok")

    with SafeHttpClient(
        settings(tmp_path), transport=httpx.MockTransport(handler), sleeper=lambda _: None
    ) as client:
        assert client.get("https://example.test/public").text == "ok"
        with pytest.raises(RobotsDeniedError):
            client.get("https://example.test/private/article")
    assert requested == ["/robots.txt", "/public"]


def test_robots_failure_stops_safely_without_target_request(tmp_path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(503, text="unavailable")

    with (
        SafeHttpClient(
            settings(tmp_path), transport=httpx.MockTransport(handler), sleeper=lambda _: None
        ) as client,
        pytest.raises(RobotsUnavailableError),
    ):
        client.get("https://example.test/article")
    assert requested == ["/robots.txt"]


def test_specific_user_agent_rule_is_applied(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: KCILNewsMonitor\nDisallow: /\nUser-agent: *\nAllow: /\n",
            )
        raise AssertionError("blocked URL must not be requested")

    with (
        SafeHttpClient(
            settings(tmp_path), transport=httpx.MockTransport(handler), sleeper=lambda _: None
        ) as client,
        pytest.raises(RobotsDeniedError),
    ):
        client.get("https://example.test/article")


def test_non_retryable_http_status_is_not_retried(tmp_path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(404, text="missing")

    with (
        SafeHttpClient(
            settings(tmp_path, retries=2),
            transport=httpx.MockTransport(handler),
            sleeper=lambda _: None,
        ) as client,
        pytest.raises(HttpRequestError, match="HTTP 404"),
    ):
        client.get("https://example.test/missing")
    assert requested == ["/robots.txt", "/missing"]


@pytest.mark.parametrize(
    "contact",
    ["bad", "a@example.org\r\nX-Test: bad", "ftp://example.org", "https://example.org/a b"],
)
def test_invalid_contact_is_rejected(contact: str) -> None:
    with pytest.raises(ContactRequiredError):
        validate_contact(contact)


def test_retry_after_is_honored_for_429(tmp_path) -> None:
    article_attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal article_attempts
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        article_attempts += 1
        if article_attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text="ok")

    with SafeHttpClient(
        settings(tmp_path, retries=1),
        transport=httpx.MockTransport(handler),
        sleeper=sleeps.append,
    ) as client:
        assert client.get("https://example.test/article").text == "ok"
    assert article_attempts == 2
    assert 7.0 in sleeps


def test_redirect_target_robots_is_rechecked(tmp_path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(f"{request.url.host}{request.url.path}")
        if request.url.path == "/robots.txt" and request.url.host == "example.test":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/robots.txt" and request.url.host == "other.test":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://other.test/private"})
        raise AssertionError("blocked redirect target must not be requested")

    with (
        SafeHttpClient(
            settings(tmp_path), transport=httpx.MockTransport(handler), sleeper=lambda _: None
        ) as client,
        pytest.raises(RobotsDeniedError),
    ):
        client.get("https://example.test/start")
    assert requested == [
        "example.test/robots.txt",
        "example.test/start",
        "other.test/robots.txt",
    ]
