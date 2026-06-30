"""GitHub profile source (unstructured group).

The network client is abstracted behind `GitHubClient` so the demo runs offline,
deterministically, and free of rate limits using saved fixtures — while a real API
call is a one-line drop-in swap (`LiveGitHubClient`). Both return the SAME shape:

    {
      "login": "...", "name": "...", "bio": "...", "blog": "...",
      "company": "...", "location": "...", "email": "...",
      "html_url": "https://github.com/...",
      "languages": {"Python": 12000, "Go": 3000},   # aggregated bytes per language
      "top_repos": ["repo-a", "repo-b"]
    }

GitHub is treated as *authoritative* for `links.github` and for programming languages.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..models import Claim, Method, SourceKind, SourceRecord
from .base import Source

_CONF = 0.8
_CONF_AUTHORITATIVE = 0.95  # github link & languages: github is the source of truth
_CONF_BIO = 0.6             # bios are loose prose -> low trust for headline
_CONF_LOCATION = 0.6        # free-text location string, lightly inferred

# US state abbreviations collide with ISO-3166 alpha-2 country codes (e.g. CA=Canada,
# GA=Gabon), so a bare 2-letter trailing token in a free-text location is ambiguous.
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


class GitHubClient(ABC):
    @abstractmethod
    def fetch(self, username: str) -> dict | None:
        ...


class FixtureGitHubClient(GitHubClient):
    """Reads pre-saved API responses from a directory (offline, deterministic)."""

    def __init__(self, fixtures_dir: str | Path):
        self.dir = Path(fixtures_dir)

    def fetch(self, username: str) -> dict | None:
        f = self.dir / f"{username}.json"
        if not f.exists():
            return None
        return json.loads(f.read_text(encoding="utf-8"))


class LiveGitHubClient(GitHubClient):  # pragma: no cover - network, not used in tests
    """Real GitHub REST client. Drop-in replacement for the fixture client."""

    def __init__(self, token: str | None = None):
        self.token = token

    def fetch(self, username: str) -> dict | None:
        import urllib.request

        headers = {"Accept": "application/vnd.github+json", "User-Agent": "eightfold-transformer"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        def _get(url: str) -> Any:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())

        try:
            user = _get(f"https://api.github.com/users/{username}")
            repos = _get(f"https://api.github.com/users/{username}/repos?per_page=100&sort=pushed")
        except Exception:
            return None
        languages: dict[str, int] = {}
        for r in repos:
            lang = r.get("language")
            if lang:
                languages[lang] = languages.get(lang, 0) + max(1, r.get("size", 1))
        user["languages"] = languages
        user["top_repos"] = [r.get("name") for r in repos[:10] if r.get("name")]
        return user


def _username_from(value: str) -> str:
    """Accept a username, a profile URL, or a fixture filename."""
    v = value.strip().rstrip("/")
    if "github.com/" in v:
        v = v.split("github.com/")[-1].split("/")[0]
    if v.endswith(".json"):
        v = v[:-5]
    return v


class GitHubSource(Source):
    kind = SourceKind.GITHUB.value

    def __init__(self, client: GitHubClient | None = None):
        self.client = client

    def _extract(self, path: Path) -> list[SourceRecord]:
        # The pipeline feeds a fixture file directly; the client abstraction is used
        # when resolving a username/URL (see `from_username`).
        profile = json.loads(path.read_text(encoding="utf-8"))
        return self._records_from_profile(profile)

    def from_username(self, username: str) -> list[SourceRecord]:
        if not self.client:
            raise RuntimeError("GitHubSource needs a client to resolve a username")
        profile = self.client.fetch(_username_from(username))
        return self._records_from_profile(profile) if profile else []

    def _records_from_profile(self, p: dict) -> list[SourceRecord]:
        if not isinstance(p, dict):
            return []
        claims: list[Claim] = []

        def add(field: str, value: Any, conf: float, span: str | None = None):
            claims.append(Claim(field=field, value=value, source=self.kind,
                                method=Method.GITHUB_API.value, raw_span=span or str(value),
                                extracted_confidence=conf))

        if p.get("name"):
            add("full_name", p["name"], _CONF)
        if p.get("html_url"):
            add("links.github", p["html_url"], _CONF_AUTHORITATIVE)
        if p.get("blog"):
            add("links.portfolio", p["blog"], _CONF)
        if p.get("email"):
            add("emails", p["email"], _CONF)
        if p.get("bio"):
            add("headline", p["bio"], _CONF_BIO)
        if p.get("location"):
            # GitHub location is a free string e.g. "Bengaluru, India" or "San Francisco, CA".
            parts = [x.strip() for x in str(p["location"]).split(",") if x.strip()]
            city = parts[0] if parts else None
            region: str | None = None
            country: str | None = None
            if len(parts) >= 3:
                region, country = parts[1], parts[-1]
            elif len(parts) == 2:
                tail = parts[1]
                # A bare 2-letter US state (e.g. "CA", "OR") is ambiguous against a country
                # code, so keep it as a region and abstain on country rather than emit a
                # confident-but-wrong country (honest-null).
                if tail.upper() in _US_STATES:
                    region = tail
                else:
                    country = tail
            loc = {"city": city, "region": region, "country": country}
            add("location", loc, _CONF_LOCATION, span=p["location"])
        # Languages -> skills (github is authoritative for what languages they use).
        for lang in (p.get("languages") or {}):
            add("skills", lang, _CONF_AUTHORITATIVE, span=f"language:{lang}")

        if not claims:
            return []
        login = p.get("login") or "user"
        return [SourceRecord(source=self.kind, record_id=f"{self.kind}:{login}", claims=claims)]
