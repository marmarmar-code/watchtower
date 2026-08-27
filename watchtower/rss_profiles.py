"""Load and validate the small directory of official RSS/Atom profiles."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import tomllib
from typing import Any
from urllib.parse import urlparse

PROFILE_PATH = Path(__file__).with_name("rss_profiles.toml")
REQUIRED_FIELDS = (
    "id",
    "name",
    "official_url",
    "owner",
    "status",
    "verified_on",
    "coverage",
)
OPTIONAL_FIELDS = ("feed_urls",)
STATUSES = {"verified", "directory"}
PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def load_profiles(path: Path = PROFILE_PATH) -> list[dict[str, Any]]:
    """Read profiles from TOML and fail closed on malformed metadata."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    profiles = data.get("profile")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("RSS profile catalog must contain at least one profile")
    for profile in profiles:
        _validate(profile)
    ids = [profile["id"] for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("RSS profile ids must be unique")
    return profiles


def _validate(profile: Any) -> None:
    if not isinstance(profile, dict) or any(
        not profile.get(field) for field in REQUIRED_FIELDS
    ):
        raise ValueError("RSS profile is missing required metadata")
    unknown = set(profile) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)
    if unknown:
        raise ValueError(f"RSS profile contains unknown fields: {sorted(unknown)}")
    if not PROFILE_ID_RE.fullmatch(str(profile["id"])):
        raise ValueError("RSS profile id must be lowercase snake_case")
    for field in ("name", "owner", "coverage"):
        if not isinstance(profile[field], str) or not profile[field].strip():
            raise ValueError(f"RSS profile {field} must be a non-empty string")
    status = profile["status"]
    if status not in STATUSES:
        raise ValueError(f"RSS profile has unknown status: {status}")
    try:
        date.fromisoformat(str(profile["verified_on"]))
    except ValueError as exc:
        raise ValueError("RSS profile verified_on must be an ISO date") from exc
    parsed = urlparse(str(profile["official_url"]))
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"RSS profile official_url must be HTTPS: {profile.get('id')}")
    feed_urls = profile.get("feed_urls", [])
    if not isinstance(feed_urls, list) or any(
        not isinstance(url, str)
        or urlparse(url).scheme != "https"
        or not urlparse(url).netloc
        for url in feed_urls
    ):
        raise ValueError(
            f"RSS profile feed_urls must contain HTTPS URLs: {profile.get('id')}"
        )
    if status == "verified" and not feed_urls:
        raise ValueError(f"verified RSS profile has no feed URL: {profile.get('id')}")


def resolve_profile_urls(
    profile_ids: list[str] | tuple[str, ...],
    *,
    path: Path = PROFILE_PATH,
) -> tuple[str, ...]:
    """Resolve ready-to-use profile IDs to their public feed URLs."""
    if not profile_ids or not all(isinstance(value, str) for value in profile_ids):
        raise ValueError("RSS profiles must be a non-empty string array")
    by_id = {row["id"]: row for row in load_profiles(path)}
    urls: list[str] = []
    for profile_id in profile_ids:
        profile = by_id.get(profile_id)
        if profile is None:
            raise ValueError(f"unknown RSS profile: {profile_id}")
        feed_urls = profile.get("feed_urls", [])
        if not feed_urls:
            raise ValueError(f"RSS profile is not ready to use directly: {profile_id}")
        urls.extend(feed_urls)
    return tuple(dict.fromkeys(urls))
