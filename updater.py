"""Conservative, read-only GitHub release checker and checked local code updater.

Update operation never modifies the persistent data folder.  Only a managed
installation with an external virtualenv can be updated by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app_paths import data_dir, install_dir
from version import __version__

_MAX_JSON = 2 * 1024 * 1024
_MAX_ASSET = 100 * 1024 * 1024
_MAX_UNCOMPRESSED = 250 * 1024 * 1024
_REQUIRED = {"uhv_sentinel.py", "updater.py", "version.py", "app_paths.py", "release_config.json", "requirements.txt", "tdk_lambda.py", "tdk_logging.py", "tdk_panel.py", "profiles.py"}
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class Release:
    version: str
    html_url: str
    asset_url: str
    checksum_url: str
    body: str
    repo: str
    asset_name: str


def version_tuple(text: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(str(text))
    if not match:
        raise ValueError(f"Invalid release version '{text}'; expected vMAJOR.MINOR.PATCH")
    return tuple(map(int, match.groups()))


def release_settings() -> dict:
    path = Path(__file__).resolve().parent / "release_config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    repo = data.get("github_repo", "")
    if not isinstance(repo, str) or not _REPOSITORY.fullmatch(repo):
        raise ValueError("Set github_repo to OWNER/REPOSITORY in release_config.json")
    return data


def _load_url(url: str, *, max_size: int) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS downloads are allowed")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/octet-stream",
        "User-Agent": "UHV-Sentinel-Updater/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            actual_url = response.geturl()
            if not actual_url.startswith("https://"):
                raise ValueError("Download redirected to an insecure URL")
            data = response.read(max_size + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("No published GitHub Release found. Create the repository and a tagged release first.") from exc
        raise RuntimeError(f"GitHub returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot connect to GitHub: {exc.reason}") from exc
    if len(data) > max_size:
        raise ValueError("GitHub response exceeds maximum allowed size")
    return data


def latest_release() -> Release:
    settings = release_settings()
    repo = settings["github_repo"]
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    payload = json.loads(_load_url(api_url, max_size=_MAX_JSON))
    version = str(payload.get("tag_name", ""))
    version_tuple(version)
    assets = {a.get("name"): a.get("browser_download_url") for a in payload.get("assets", [])}
    zip_name = settings.get("release_asset", "uhv-sentinel-release.zip")
    checksum_name = settings.get("checksum_asset", "SHA256SUMS.txt")
    if zip_name not in assets or checksum_name not in assets:
        raise ValueError(f"Release {version} must contain both {zip_name} and {checksum_name} assets")
    if not all(isinstance(assets[name], str) and assets[name].startswith("https://github.com/")
               for name in (zip_name, checksum_name)):
        raise ValueError("Unexpected release asset URL; refusing download")
    return Release(version=version.lstrip("v"), html_url=str(payload.get("html_url", "")),
                   asset_url=assets[zip_name], checksum_url=assets[checksum_name],
                   body=str(payload.get("body") or "")[:5000], repo=repo, asset_name=zip_name)


def newer_release(release: Release) -> bool:
    return version_tuple(release.version) > version_tuple(__version__)


def checksum_for(checksum_text: str, filename: str) -> str:
    matches = []
    for line in checksum_text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(\S+)", line.strip())
        if match and match.group(2) == filename:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one SHA-256 checksum for {filename}")
    return matches[0]


def validate_archive(archive: str | Path, expected_version: str) -> None:
    total = 0
    seen = set()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            name = member.filename
            posix = PurePosixPath(name)
            if not name or name.startswith("/") or "\\" in name or ".." in posix.parts:
                raise ValueError(f"Unsafe ZIP archive path: {name}")
            if posix.parts and ":" in posix.parts[0]:
                raise ValueError("Drive-qualified path inside ZIP")
            # Reject UNIX symlinks (also stops escaping the installation via links).
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP archive contains a symbolic link")
            if not member.is_dir():
                if name in seen:
                    raise ValueError("Duplicate path in release archive")
                seen.add(name)
                total += member.file_size
                if member.file_size > _MAX_ASSET or total > _MAX_UNCOMPRESSED:
                    raise ValueError("Release archive is too large")
        missing = _REQUIRED - seen
        if missing:
            raise ValueError(f"Release ZIP is missing files: {', '.join(sorted(missing))}")
        ver = zf.read("version.py").decode("utf-8")
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', ver, re.MULTILINE)
        if not match or version_tuple(match.group(1)) != version_tuple(expected_version):
            raise ValueError("Release ZIP version does not match its GitHub release tag")
        cfg = json.loads(zf.read("release_config.json"))
        if cfg.get("github_repo") != release_settings().get("github_repo"):
            raise ValueError("Release ZIP targets a different GitHub repository")


def prepare_release(release: Release) -> dict:
    if not newer_release(release):
        raise ValueError("This release is not newer than the installed version")
    # The checksum and archive are both served by GitHub; checksum catches
    # incomplete/corrupt assets, not a compromised publisher/repository.
    checksum_doc = _load_url(release.checksum_url, max_size=100_000).decode("utf-8")
    expected_hash = checksum_for(checksum_doc, release.asset_name)
    payload = _load_url(release.asset_url, max_size=_MAX_ASSET)
    actual_hash = hashlib.sha256(payload).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("Downloaded release failed SHA-256 verification")
    folder = data_dir() / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="pending-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        validate_archive(tmp, release.version)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {"archive": tmp, "version": release.version, "sha256": expected_hash}


def write_update_result(success: bool, details: str) -> None:
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "last_update.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"success": bool(success), "details": details, "when": time.time()}), encoding="utf-8")
    tmp.replace(path)


def take_update_result() -> dict | None:
    path = data_dir() / "last_update.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)


def apply_prepared_update(*, archive: str, version: str, sha256: str) -> None:
    """Called ONLY after the GUI closes, never from a serial monitoring thread."""
    root = install_dir()
    current = root / "app"
    backup = root / "previous_app"
    marker = root / "install.json"
    if not marker.is_file() or Path(__file__).resolve().parent != current.resolve():
        raise RuntimeError("Update requires a normal installed copy. Run INSTALL instead of updating a source checkout.")
    archive_path = Path(archive).resolve()
    update_root = (data_dir() / "updates").resolve()
    if update_root not in archive_path.parents or not archive_path.is_file():
        raise ValueError("Invalid prepared update location")
    if not _HEX.fullmatch(sha256) or hashlib.sha256(archive_path.read_bytes()).hexdigest() != sha256.lower():
        raise ValueError("Release archive checksum changed after verification")
    if version_tuple(version) <= version_tuple(__version__):
        raise ValueError("Refusing an older or same-version replacement")
    validate_archive(archive_path, version)
    if backup.exists():
        shutil.rmtree(backup)
    stage = Path(tempfile.mkdtemp(prefix="staging-", dir=root))
    swapped = False
    try:
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(stage)
        # Install dependencies before replacing current files.  Runtime is
        # separate from the application folder and survives code upgrades.
        import subprocess
        from app_paths import runtime_python
        executable = runtime_python(root)
        if not executable.is_file():
            raise RuntimeError("Installed Python runtime missing; reinstall the application")
        subprocess.run([str(executable), "-m", "pip", "install", "-r", str(stage / "requirements.txt")],
                       check=True, timeout=180)
        current.rename(backup)
        try:
            stage.rename(current)
            swapped = True
        except Exception:
            backup.rename(current)
            raise
        write_update_result(True, f"UHV Sentinel updated to v{version}; previous version saved in installation/previous_app")
    except Exception:
        if not current.exists() and backup.exists():
            backup.rename(current)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        archive_path.unlink(missing_ok=True)
