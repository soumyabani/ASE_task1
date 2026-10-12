#!/usr/bin/env python3
"""Verify requirements.txt entries against PyPI."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PYPI_PROJECT_URL = "https://pypi.org/pypi/{name}/json"
PYPI_RELEASE_URL = "https://pypi.org/pypi/{name}/{version}/json"
USER_AGENT = "requirements-pypi-verify/1.0"

# PEP 508 name + optional extras + == pinned version
PINNED_REQUIREMENT = re.compile(
    r"^\s*"
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[^\]]+\])?"
    r"\s*==\s*"
    r"(?P<version>[^\s;#]+)"
    r"\s*(?:#.*)?$"
)

UNPINNED_NAME = re.compile(
    r"^\s*"
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[^\]]+\])?"
    r"\s*(?:[<>=!~]|$|\s|#)"
)


def fetch_json(url: str) -> dict | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def strip_inline_comment(line: str) -> str:
    if "#" not in line:
        return line.strip()
    # Keep # inside URLs (rare in requirements.txt); split on whitespace+# only.
    for idx, char in enumerate(line):
        if char == "#" and (idx == 0 or line[idx - 1].isspace()):
            return line[:idx].strip()
    return line.strip()


def parse_requirement_line(line: str) -> tuple[str, str | None] | None:
    """Return (normalized_name, pinned_version_or_none) or None if not a package line."""
    cleaned = strip_inline_comment(line)
    if not cleaned:
        return None
    if cleaned.startswith("-"):
        return None

    pinned = PINNED_REQUIREMENT.match(cleaned)
    if pinned:
        return pinned.group("name").lower(), pinned.group("version")

    if "@" in cleaned or "://" in cleaned:
        return None

    loose = UNPINNED_NAME.match(cleaned)
    if loose:
        return loose.group("name").lower(), None

    return None


def release_upload_time(project_json: dict, version: str) -> datetime | None:
    releases = project_json.get("releases", {})
    if version not in releases:
        return None

    files = releases.get(version) or []
    if not files:
        release_json = fetch_json(
            PYPI_RELEASE_URL.format(
                name=urllib.parse.quote(project_json["info"]["name"], safe=""),
                version=urllib.parse.quote(version, safe=""),
            )
        )
        if release_json:
            files = release_json.get("urls") or []

    if not files:
        return None

    upload_time = files[0].get("upload_time") or files[0].get("upload_time_iso")
    if not upload_time:
        return None

    if upload_time.endswith("Z"):
        upload_time = upload_time[:-1] + "+00:00"
    parsed = datetime.fromisoformat(upload_time)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def version_exists(project_json: dict, version: str) -> bool:
    releases = project_json.get("releases", {})
    if version in releases:
        return True
    release_json = fetch_json(
        PYPI_RELEASE_URL.format(
            name=urllib.parse.quote(project_json["info"]["name"], safe=""),
            version=urllib.parse.quote(version, safe=""),
        )
    )
    return release_json is not None


def read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        return handle.readlines()


def verify(path: str) -> int:
    lines = read_lines(path)
    packages_missing: list[str] = []
    versions_missing: list[str] = []
    valid_pinned: list[tuple[str, str, datetime]] = []

    for line in lines:
        parsed = parse_requirement_line(line)
        if parsed is None:
            continue

        name, pinned_version = parsed
        project_json = fetch_json(PYPI_PROJECT_URL.format(name=urllib.parse.quote(name, safe="")))

        if project_json is None:
            packages_missing.append(name)
            continue

        if pinned_version is None:
            continue

        if not version_exists(project_json, pinned_version):
            versions_missing.append(f"{name}=={pinned_version}")
            continue

        uploaded = release_upload_time(project_json, pinned_version)
        if uploaded is not None:
            canonical = project_json["info"]["name"]
            valid_pinned.append((canonical, pinned_version, uploaded))

    dependencies_checked = len(lines)

    print(f"Dependencies checked: {dependencies_checked}")

    if packages_missing:
        print(f"Packages not on PyPI: {', '.join(packages_missing)}")
    else:
        print("Packages not on PyPI: (none)")

    if versions_missing:
        print(f"Versions not on PyPI: {', '.join(versions_missing)}")
    else:
        print("Versions not on PyPI: (none)")

    if valid_pinned:
        oldest = min(valid_pinned, key=lambda item: item[2])
        name, version, uploaded = oldest
        print(f"Oldest pinned release: {name}=={version}({uploaded.year})")
    else:
        print("Oldest pinned release: (none)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify requirements.txt against PyPI.")
    parser.add_argument(
        "requirements",
        nargs="?",
        default="requirements.txt",
        help="Path to requirements file (default: requirements.txt)",
    )
    args = parser.parse_args()

    try:
        return verify(args.requirements)
    except FileNotFoundError:
        print(f"Error: file not found: {args.requirements}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Error: failed to reach PyPI: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
