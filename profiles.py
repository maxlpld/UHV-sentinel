"""Portable, schema-versioned UHV Sentinel workspaces (no passwords or live outputs)."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Dict
from app_paths import data_dir

PROFILE_FORMAT = "uhv-sentinel-profile"
PROFILE_VERSION = 1
MAX_PROFILE_BYTES = 512 * 1024


def profiles_dir() -> Path:
    """Inside the app's persistent data folder; survives an application update."""
    folder = data_dir() / "profiles"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def slug(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(name).strip()).strip("-_ ")[:70]
    if not clean:
        raise ValueError("Profile name cannot be empty")
    return clean


def sanitize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Allowlist configuration keys, strip all sensitive and dangerous runtime state."""
    if not isinstance(config, dict):
        raise ValueError("Invalid configuration")
    safe = {"controllers": [], "alert_settings": {}, "layout": {}}
    seen = set()
    allowed_types = {
        "AML NGC3", "Pfeiffer DualGauge TPG 262", "Agilent 4UHV 2x80W NEG",
        "EpiMax PVCi (RJ45 serial)", "IMPAC IN 140/5 / IS 140 / IGA 140 pyrometer",
        "IMPAC IS 140 / IGA 140 pyrometer", "TDK-Lambda power supply",
    }
    for item in config.get("controllers", []):
        if not isinstance(item, dict):
            continue
        port = str(item.get("port", "")).strip()[:256]
        if not port or port in seen:
            continue
        seen.add(port)
        device_type = str(item.get("device_type", "AML NGC3"))
        if device_type not in allowed_types:
            continue
        state = item.get("state", {})
        if not isinstance(state, dict):
            state = {}
        # Keep configuration, not recent commands, remote/armed flags or outputs.
        allow_state = ("graph_range", "selected_ig", "address", "pressure_units", "protocol",
                       "poll_interval_s", "sample_interval_s", "current_limit_a", "voltage_limit_v")
        state = {key: state[key] for key in allow_state if key in state}
        safe["controllers"].append({
            "name": str(item.get("name", port))[:128], "port": port,
            "baudrate": int(item.get("baudrate", 9600)),
            "device_type": device_type, "state": state,
        })
    settings = config.get("alert_settings", {})
    if isinstance(settings, dict):
        safe["alert_settings"] = {key: value for key, value in settings.items()
                                  if key not in ("smtp_password", "password", "token", "secret")}
        # Never activate unattended hardware control from an imported profile.
        safe["alert_settings"]["remote_control_enabled"] = False
    layout = config.get("layout", {})
    if isinstance(layout, dict):
        columns = layout.get("columns", 2)
        try:
            columns = int(columns)
        except (ValueError, TypeError):
            columns = 2
        safe["layout"] = {
            "columns": min(3, max(1, columns)),
            "window_geometry": str(layout.get("window_geometry", ""))[:80],
            "scroll_fraction": 0.0,
        }
    return safe


def make_profile(name: str, config: Dict[str, Any], *, theme_path: Path | None = None) -> Dict[str, Any]:
    profile = {"format": PROFILE_FORMAT, "schema_version": PROFILE_VERSION,
               "name": str(name).strip()[:100], "config": sanitize_config(config)}
    if theme_path is not None and theme_path.is_file():
        raw = theme_path.read_bytes()
        if len(raw) > 128 * 1024:
            raise ValueError("Theme file too large for a profile")
        theme = json.loads(raw.decode("utf-8"))
        if not isinstance(theme, dict):
            raise ValueError("Theme must be a JSON object")
        profile["theme_asset"] = {"filename": theme_path.name, "document": theme}
    return profile


def validate_profile(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format") != PROFILE_FORMAT:
        raise ValueError("Not a UHV Sentinel profile")
    if payload.get("schema_version") != PROFILE_VERSION:
        raise ValueError("Unsupported profile schema version")
    if not isinstance(payload.get("config"), dict):
        raise ValueError("Invalid profile configuration")
    safe = make_profile(str(payload.get("name", "Imported")), payload["config"])
    theme = payload.get("theme_asset")
    if theme is not None:
        if (not isinstance(theme, dict) or not isinstance(theme.get("document"), dict)
                or not isinstance(theme.get("filename"), str)):
            raise ValueError("Malformed embedded theme")
        name = Path(theme["filename"]).name
        if name != theme["filename"] or not name.endswith(".json"):
            raise ValueError("Unsafe embedded theme filename")
        safe["theme_asset"] = {"filename": name, "document": theme["document"]}
    return safe


def save_profile(name: str, config: Dict[str, Any], *, dest: Path | None = None,
                 theme_path: Path | None = None) -> Path:
    path = dest or (profiles_dir() / f"{slug(name)}.json")
    return save_profile_payload(make_profile(name, config, theme_path=theme_path), path)


def save_profile_payload(payload: Dict[str, Any], path: Path) -> Path:
    payload = validate_profile(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_PROFILE_BYTES:
        raise ValueError("Profile too large")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(encoded)
    tmp.replace(path)
    return path


def load_profile(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError("Profile too large")
    return validate_profile(json.loads(path.read_text(encoding="utf-8")))


def install_embedded_theme(profile: Dict[str, Any]) -> str | None:
    """Copy validated theme JSON into persistent app-data/themes; return basename."""
    payload = validate_profile(profile)
    embedded = payload.get("theme_asset")
    if not embedded:
        return None
    path = data_dir() / "themes" / (slug(payload["name"]) + ".theme.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(embedded["document"], indent=2).encode("utf-8")
    if len(document) > 128 * 1024:
        raise ValueError("Embedded theme exceeds size limit")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(document)
    tmp.replace(path)
    return path.name
