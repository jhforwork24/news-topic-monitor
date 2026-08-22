from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .constants import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_INTERVAL_SECONDS,
    USER_AGENT_NAME,
    project_root,
)


class ContactRequiredError(ValueError):
    pass


def validate_contact(value: str | None) -> str:
    contact = (value or "").strip()
    if not contact:
        raise ContactRequiredError(
            "MONITOR_CONTACT is required; set a public email address or contact URL"
        )
    if any(character in contact for character in "\r\n\t"):
        raise ContactRequiredError("MONITOR_CONTACT must not contain control characters")
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact):
        return contact
    parsed = urlparse(contact)
    if (
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and not any(character.isspace() for character in contact)
    ):
        return contact
    raise ContactRequiredError("MONITOR_CONTACT must be an email address or public HTTP(S) URL")


@dataclass(frozen=True)
class Settings:
    root: Path
    contact: str
    request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    max_discovery_children: int = 20
    hani_max_pages: int = 50

    @property
    def user_agent(self) -> str:
        return f"{USER_AGENT_NAME} (+{self.contact})"

    @classmethod
    def from_env(cls, root: Path | None = None) -> Settings:
        return cls(
            root=root or project_root(),
            contact=validate_contact(os.getenv("MONITOR_CONTACT")),
            request_interval_seconds=max(
                DEFAULT_REQUEST_INTERVAL_SECONDS,
                float(os.getenv("REQUEST_INTERVAL_SECONDS", DEFAULT_REQUEST_INTERVAL_SECONDS)),
            ),
            connect_timeout_seconds=float(
                os.getenv("CONNECT_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT_SECONDS)
            ),
            read_timeout_seconds=float(
                os.getenv("READ_TIMEOUT_SECONDS", DEFAULT_READ_TIMEOUT_SECONDS)
            ),
            max_retries=max(0, int(os.getenv("MAX_RETRIES", DEFAULT_MAX_RETRIES))),
            max_discovery_children=max(1, int(os.getenv("MAX_DISCOVERY_CHILDREN", "20"))),
            hani_max_pages=max(1, int(os.getenv("HANI_MAX_PAGES", "50"))),
        )
