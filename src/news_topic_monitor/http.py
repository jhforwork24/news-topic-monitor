from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .settings import Settings

LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RobotsUnavailableError(RuntimeError):
    pass


class RobotsDeniedError(PermissionError):
    pass


class HttpRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    status: str
    robots_url: str
    detail: str | None = None


@dataclass
class CachedRobots:
    parser: RobotFileParser | None
    fetched_at: datetime
    error: str | None
    robots_url: str


class DomainRateLimiter:
    def __init__(self, interval_seconds: float, clock=time.monotonic, sleeper=time.sleep) -> None:
        self.interval_seconds = interval_seconds
        self.clock = clock
        self.sleeper = sleeper
        self._last_request: dict[str, float] = {}
        self._lock = Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = self.clock()
            previous = self._last_request.get(host)
            if previous is not None:
                delay = self.interval_seconds - (now - previous)
                if delay > 0:
                    self.sleeper(delay)
                    now = self.clock()
            self._last_request[host] = now


class SafeHttpClient:
    """HTTP client that checks robots.txt before every non-robots request.

    Robots retrieval failures are cached as failures for this run and all requests to
    that origin fail closed. One client processes requests serially, so the per-domain
    concurrency limit is one; DomainRateLimiter additionally enforces spacing.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.settings = settings
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
            write=settings.read_timeout_seconds,
            pool=settings.connect_timeout_seconds,
        )
        self.client = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": (
                    "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.2"
                ),
            },
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )
        self.sleeper = sleeper
        self.limiter = DomainRateLimiter(settings.request_interval_seconds, sleeper=sleeper)
        self._robots: dict[str, CachedRobots] = {}

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def robots_decision(self, url: str) -> RobotsDecision:
        origin, robots_url = self._origin_and_robots(url)
        cached = self._robots.get(origin)
        if cached is None:
            cached = self._fetch_robots(origin, robots_url)
            self._robots[origin] = cached
        if cached.parser is None:
            return RobotsDecision(
                allowed=False,
                status="unavailable",
                robots_url=cached.robots_url,
                detail=cached.error,
            )
        allowed = cached.parser.can_fetch(self.settings.user_agent, url)
        return RobotsDecision(
            allowed=allowed,
            status="allowed" if allowed else "blocked",
            robots_url=cached.robots_url,
        )

    def get(self, url: str, *, purpose: str = "metadata") -> httpx.Response:
        current = url
        for _ in range(6):
            decision = self.robots_decision(current)
            if decision.status == "unavailable":
                raise RobotsUnavailableError(
                    f"robots.txt unavailable for {urlsplit(current).netloc}: {decision.detail}"
                )
            if not decision.allowed:
                raise RobotsDeniedError(f"robots.txt blocks {purpose} URL: {current}")
            response = self._request_with_retries(current)
            if not response.is_redirect:
                return response
            location = response.headers.get("Location")
            if not location:
                raise HttpRequestError(f"redirect has no Location header: {current}")
            current = str(response.url.join(location))
        raise HttpRequestError(f"too many redirects: {url}")

    def _fetch_robots(self, origin: str, robots_url: str) -> CachedRobots:
        try:
            current = robots_url
            response: httpx.Response | None = None
            for _ in range(6):
                response = self._request_with_retries(current)
                if not response.is_redirect:
                    break
                location = response.headers.get("Location")
                if not location:
                    raise HttpRequestError(f"robots redirect has no Location header: {current}")
                redirected = str(response.url.join(location))
                redirected_origin, _ = self._origin_and_robots(redirected)
                if redirected_origin != origin:
                    raise HttpRequestError("cross-origin robots.txt redirect rejected")
                current = redirected
            if response is None or response.is_redirect:
                raise HttpRequestError("too many robots.txt redirects")
            if response.status_code != 200:
                raise HttpRequestError(f"robots.txt returned HTTP {response.status_code}")
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            return CachedRobots(
                parser=parser,
                fetched_at=datetime.now(UTC),
                error=None,
                robots_url=str(response.url),
            )
        except (httpx.HTTPError, HttpRequestError) as exc:
            LOGGER.warning("robots.txt unavailable for %s: %s", origin, exc)
            return CachedRobots(
                parser=None,
                fetched_at=datetime.now(UTC),
                error=str(exc),
                robots_url=robots_url,
            )

    def _request_with_retries(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc.lower()
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            self.limiter.wait(host)
            try:
                response = self.client.get(url)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    break
                self.sleeper(min(2**attempt, 8))
                continue
            if response.is_redirect:
                return response
            if response.status_code in RETRYABLE_STATUS:
                last_error = httpx.HTTPStatusError(
                    f"retryable HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
                if attempt < self.settings.max_retries:
                    self.sleeper(self._retry_delay(response, attempt))
                    continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise HttpRequestError(f"GET failed: {url}: HTTP {response.status_code}") from exc
            return response
        raise HttpRequestError(f"GET failed after retries: {url}: {last_error}") from last_error

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 120.0)
            except ValueError:
                try:
                    target = parsedate_to_datetime(retry_after).astimezone(UTC)
                    return min(max((target - datetime.now(UTC)).total_seconds(), 0.0), 120.0)
                except (TypeError, ValueError):
                    pass
        return min(2**attempt, 8)

    @staticmethod
    def _origin_and_robots(url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"invalid URL: {url}")
        origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
        return origin, f"{origin}/robots.txt"
