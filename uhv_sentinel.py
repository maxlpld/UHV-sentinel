#!/usr/bin/env python3
"""UHV Sentinel multi-instrument vacuum pressure and diagnostics monitor."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import math
import os
import platform
import queue
import re
import smtplib
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - user-facing fallback
    raise SystemExit(
        "pyserial is required. Install it with:\n\n"
        "    pip install pyserial\n"
    ) from exc

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from impac_pyrometer import Impac140, BAUDS as IMPAC_BAUDS, T90, CLEAR, OPTIONAL_SETTINGS
from vacuum_extra_devices import Agilent4UHVDevice, EpimaxPVCDevice, ExtendedStatus
from app_paths import config_path, data_dir, install_dir, runtime_python
from version import __version__
import updater
from tdk_panel import TDKPanel, TDK_DEVICE_TYPE, TDK_HISTORY
import profiles


APP_TITLE = f"UHV Sentinel  ·  v{__version__}"
MANUAL_URL = "https://arunmicro.com/documentation/Manual_NGC3.pdf"
README_FILENAME = "README.md"
MANUAL_FILENAME = "Manual_NGC3.pdf"
IMPAC_MANUAL_FILENAME = "IN-1405-Series-Manual.pdf"
IMPAC_MANUAL_URL = "https://www.control-metrix.com/wp-content/uploads/2023/03/IN-1405-Series-Manual.pdf"
CONFIG_PATH = config_path()
DATA_DIR = data_dir()
PRESSURE_DB_PATH = DATA_DIR / "pressure_history.sqlite3"
SMTP_PASSWORD_ENV = "UHV_SENTINEL_SMTP_PASSWORD"
DEFAULT_BAUD = 9600
POLL_INTERVAL_S = 1.0
SERIAL_TIMEOUT_S = 0.25
STATUS_READ_TIMEOUT_S = 1.8
STATUS_IDLE_DONE_S = 0.18
GRAPH_REFRESH_INTERVAL_S = 5.0
GRAPH_QUERY_CACHE_S = 15.0
EVENTS_PER_TICK = 40
CONFIG_SAVE_DEBOUNCE_MS = 750
WEB_MONITOR_PORT_START = 8765
WEB_MONITOR_PORT_ATTEMPTS = 10
WEB_GRAPH_MAX_POINTS = 1400

# University of Basel visual theme.
# Tkinter falls back automatically if Inter / PT Serif are not installed.
UNIBAS_MINT = "#A5D7D2"
UNIBAS_MINT_LIGHT = "#D2EBE9"
UNIBAS_RED = "#D20537"
UNIBAS_ANTHRAZIT = "#2D373C"
UNIBAS_ANTHRAZIT_LIGHT = "#46505A"
UNIBAS_BLACK = "#000000"
UNIBAS_WHITE = "#FFFFFF"
UNIBAS_GRAY1 = "#333333"
UNIBAS_GRAY2 = "#666666"
UNIBAS_GRAY3 = "#777777"
UNIBAS_GRAY4 = "#C9C9C9"
UNIBAS_GRAY5 = "#D9D9D9"
UNIBAS_GRAY6 = "#EAEBEC"

FONT_H1 = ("PT Serif", 18)
FONT_H2 = ("PT Serif", 18)
FONT_H5 = ("PT Serif", 13)
FONT_H6 = ("PT Serif", 12)
FONT_BODY = ("Inter", 11)
FONT_BUTTON = ("Inter", 11)
FONT_CAPTION = ("Inter", 9)

CURRENT_DARK_MODE = False
PANEL_MIN_WIDTH = 520
GRAPH_CANVAS_WIDTH = 460
GRAPH_CANVAS_HEIGHT = 260
GRAPH_GAUGE_KEYS = ["IG1", "IG2", "Pirani 1", "Pirani 2", "Active Gauge"]

# Button colours.  tk.Button is used for the ion-gauge controls because ttk
# colours are ignored by some themes on Windows/macOS.
BTN_BG = UNIBAS_WHITE
BTN_FG = UNIBAS_ANTHRAZIT
BTN_DISABLED_BG = UNIBAS_GRAY5
BTN_SELECTED_GREEN = UNIBAS_MINT
BTN_OFF_RED = UNIBAS_RED
BTN_AUTO_BLUE = UNIBAS_MINT
BTN_PROMPT_ORANGE = UNIBAS_MINT_LIGHT
BTN_SELECTED_FG = UNIBAS_ANTHRAZIT

# ttk action-button colours.  These are intentionally separate from the
# ion-gauge control button colours because toolbar/action buttons should keep
# the old University of Basel style while the ion buttons encode state.
ACTION_BUTTON_BG = UNIBAS_ANTHRAZIT
ACTION_BUTTON_FG = UNIBAS_WHITE
ACTION_BUTTON_ACTIVE_BG = UNIBAS_ANTHRAZIT_LIGHT
ACTION_BUTTON_ACTIVE_FG = UNIBAS_WHITE
PRIMARY_BUTTON_BG = UNIBAS_RED
PRIMARY_BUTTON_FG = UNIBAS_WHITE
PRIMARY_BUTTON_ACTIVE_BG = "#B0042E"
PRIMARY_BUTTON_ACTIVE_FG = UNIBAS_WHITE
GHOST_BUTTON_BG = UNIBAS_WHITE
GHOST_BUTTON_FG = UNIBAS_GRAY1
GHOST_BUTTON_ACTIVE_BG = UNIBAS_MINT_LIGHT
GHOST_BUTTON_ACTIVE_FG = UNIBAS_ANTHRAZIT


STATE_BYTE_FLAGS = [
    (4, "Remote mode"),
    (5, "NGC3 bit"),
    (6, "IG2 selected"),
    (7, "Ion gauge disconnected"),
]

ERROR_BYTE_FLAGS = [
    (0, "Gauge-specific error"),
    (1, "Over-temperature trip"),
    (2, "Bake error"),
    (3, "Temperature warning"),
    (6, "NGC3 bit"),
]

ION_STATUS_FLAGS = [
    (0, "Gauge in emission"),
    (2, "Controlling bakeout"),
    (5, "Filament 2"),
    (6, "NGC3 bit"),
]

PIRANI_STATUS_FLAGS = [
    (0, "Gauge operating"),
]

ION_ERROR_FLAGS = [
    (0, "Filament open-circuit"),
    (1, "Overemission"),
    (2, "Underemission"),
    (3, "Overpressure"),
    (4, "Interlock/autostart prevents starting"),
    (6, "NGC3 bit"),
    (7, "Filament/leads"),
]

PIRANI_ERROR_FLAGS = [
    (0, "Pirani open-circuit"),
    (6, "NGC3 bit"),
]

GAUGE_NAME_BY_NUMBER = {
    "1": "IG1",
    "2": "Pirani 1",
    "3": "Pirani 2",
    "4": "Active Gauge",
    "5": "IG2",
}

UNITS = {
    "M": "mBar",
    "T": "Torr",
    "P": "Pa",
}

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
ALERT_HISTORY_LIMIT = 200
DEFAULT_IG_SENSITIVITY_MBAR_INV = 19.0
GRAPH_RANGE_OPTIONS = ["All", "48 h", "24 h", "1 h"]
GRAPH_RANGE_SECONDS = {"All": None, "48 h": 48 * 3600, "24 h": 24 * 3600, "1 h": 3600}
GRAPH_COLORS = [UNIBAS_RED, UNIBAS_ANTHRAZIT, UNIBAS_MINT, UNIBAS_ANTHRAZIT_LIGHT, UNIBAS_GRAY2]
THEME_FILE_PATTERNS = ("*_theme.json", "*theme*.json", "*.theme.json")
ACTIVE_THEME_FILE = ""

NGC3_DEVICE_TYPE = "AML NGC3"
PFEIFFER_DEVICE_TYPE = "Pfeiffer DualGauge TPG 262"
IMPAC_DEVICE_TYPE = "IMPAC IN 140/5 / IS 140 / IGA 140 pyrometer"
LEGACY_IMPAC_DEVICE_TYPES = {"IMPAC IS 140 / IGA 140 pyrometer", IMPAC_DEVICE_TYPE}
AGILENT_DEVICE_TYPE = "Agilent 4UHV 2x80W NEG"
EPIMAX_DEVICE_TYPE = "EpiMax PVCi (RJ45 serial)"
DEVICE_TYPE_OPTIONS = [NGC3_DEVICE_TYPE, PFEIFFER_DEVICE_TYPE, AGILENT_DEVICE_TYPE,
                       EPIMAX_DEVICE_TYPE, IMPAC_DEVICE_TYPE, TDK_DEVICE_TYPE]
PFEIFFER_GAUGE_KEYS = ["Channel 1", "Channel 2"]
PFEIFFER_STATUS_LABELS = {
    0: "Measurement data okay",
    1: "Underrange",
    2: "Overrange",
    3: "Sensor error",
    4: "Sensor off",
    5: "No sensor",
    6: "Identification error",
}


def load_saved_dark_mode() -> bool:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return bool(data.get("alert_settings", {}).get("dark_mode", False))
    except Exception:
        return False


def load_saved_theme_file() -> str:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return str(data.get("alert_settings", {}).get("theme_file", "") or "")
    except Exception:
        return ""


def theme_directory() -> Path:
    try:
        return Path(__file__).resolve().parent
    except Exception:
        return Path.cwd()


def list_theme_files() -> List[Path]:
    """Return importable theme files next to the UHV Sentinel program."""
    folders = [theme_directory(), DATA_DIR / "themes"]
    found: Dict[str, Path] = {}
    try:
        for folder in folders:
            for pattern in THEME_FILE_PATTERNS:
                for path in folder.glob(pattern):
                    if path.is_file() and path.suffix.lower() == ".json":
                        found[path.name] = path
    except Exception:
        return []
    return [found[name] for name in sorted(found, key=str.lower)]


def resolve_theme_file(theme_file: str = "") -> Optional[Path]:
    """Resolve the selected theme file. If only one theme exists, use it by default."""
    files = list_theme_files()
    if theme_file and theme_file != "Built-in":
        candidate = Path(theme_file)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        for path in files:
            if path.name == theme_file or path.stem == theme_file:
                return path
    if len(files) == 1:
        return files[0]
    for preferred in ("Unibasel_theme.json", "University_of_Basel_theme.json", "unibasel_theme.json"):
        for path in files:
            if path.name.lower() == preferred.lower():
                return path
    return None


def _theme_color(data: Dict[str, Any], mode: str, key: str, default: str, *aliases: str) -> str:
    """Read a colour from a theme file while accepting a few simple aliases."""
    section = data.get(mode, {}) if isinstance(data.get(mode, {}), dict) else {}
    colors = section.get("colors", section) if isinstance(section.get("colors", section), dict) else {}
    top_colors = data.get("colors", {}) if isinstance(data.get("colors", {}), dict) else {}
    for source in (colors, top_colors, data):
        if not isinstance(source, dict):
            continue
        for name in (key, *aliases):
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return default


def _theme_font(data: Dict[str, Any], key: str, default: Tuple[str, int]) -> Tuple[str, int]:
    fonts = data.get("fonts", {}) if isinstance(data.get("fonts", {}), dict) else {}
    value = fonts.get(key)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (str(value[0]), int(value[1]))
        except Exception:
            return default
    return default


def apply_color_mode(dark_mode: bool, theme_file: str = "") -> None:
    global CURRENT_DARK_MODE, ACTIVE_THEME_FILE
    global UNIBAS_MINT, UNIBAS_MINT_LIGHT, UNIBAS_RED, UNIBAS_ANTHRAZIT, UNIBAS_ANTHRAZIT_LIGHT
    global UNIBAS_BLACK, UNIBAS_WHITE, UNIBAS_GRAY1, UNIBAS_GRAY2, UNIBAS_GRAY3, UNIBAS_GRAY4, UNIBAS_GRAY5, UNIBAS_GRAY6
    global BTN_BG, BTN_FG, BTN_DISABLED_BG, BTN_SELECTED_GREEN, BTN_OFF_RED, BTN_AUTO_BLUE
    global BTN_PROMPT_ORANGE, BTN_SELECTED_FG, GRAPH_COLORS
    global ACTION_BUTTON_BG, ACTION_BUTTON_FG, ACTION_BUTTON_ACTIVE_BG, ACTION_BUTTON_ACTIVE_FG
    global PRIMARY_BUTTON_BG, PRIMARY_BUTTON_FG, PRIMARY_BUTTON_ACTIVE_BG, PRIMARY_BUTTON_ACTIVE_FG
    global GHOST_BUTTON_BG, GHOST_BUTTON_FG, GHOST_BUTTON_ACTIVE_BG, GHOST_BUTTON_ACTIVE_FG
    global FONT_H1, FONT_H2, FONT_H5, FONT_H6, FONT_BODY, FONT_BUTTON, FONT_CAPTION
    CURRENT_DARK_MODE = bool(dark_mode)

    # Built-in University of Basel fallback. External theme files override these values below.
    UNIBAS_MINT = "#A5D7D2"
    UNIBAS_MINT_LIGHT = "#D2EBE9"
    UNIBAS_RED = "#D20537"
    UNIBAS_ANTHRAZIT = "#2D373C"
    UNIBAS_ANTHRAZIT_LIGHT = "#46505A"
    UNIBAS_BLACK = "#000000"
    UNIBAS_GRAY4 = "#C9C9C9"
    UNIBAS_GRAY5 = "#D9D9D9"

    if dark_mode:
        UNIBAS_GRAY6 = "#151A1D"       # window background
        UNIBAS_WHITE = "#20282D"       # card/canvas background in this GUI theme system
        UNIBAS_GRAY1 = "#F4F4F4"       # main text
        UNIBAS_GRAY2 = "#C9D0D3"       # muted text / axes
        UNIBAS_GRAY3 = "#C9D0D3"       # disabled text
        BTN_BG = "#46505A"
        BTN_FG = "#FFFFFF"
        BTN_DISABLED_BG = "#2D373C"
        BTN_SELECTED_GREEN = UNIBAS_MINT
        BTN_OFF_RED = UNIBAS_RED
        BTN_AUTO_BLUE = UNIBAS_MINT
        BTN_PROMPT_ORANGE = "#344146"
        BTN_SELECTED_FG = "#000000"
        ACTION_BUTTON_BG = UNIBAS_ANTHRAZIT
        ACTION_BUTTON_FG = UNIBAS_WHITE
        ACTION_BUTTON_ACTIVE_BG = UNIBAS_ANTHRAZIT_LIGHT
        ACTION_BUTTON_ACTIVE_FG = UNIBAS_WHITE
        PRIMARY_BUTTON_BG = UNIBAS_RED
        PRIMARY_BUTTON_FG = UNIBAS_WHITE
        PRIMARY_BUTTON_ACTIVE_BG = "#B0042E"
        PRIMARY_BUTTON_ACTIVE_FG = UNIBAS_WHITE
        GHOST_BUTTON_BG = UNIBAS_WHITE
        GHOST_BUTTON_FG = UNIBAS_GRAY1
        GHOST_BUTTON_ACTIVE_BG = UNIBAS_MINT_LIGHT
        GHOST_BUTTON_ACTIVE_FG = UNIBAS_ANTHRAZIT
    else:
        UNIBAS_GRAY6 = "#EAEBEC"
        UNIBAS_WHITE = "#FFFFFF"
        UNIBAS_GRAY1 = "#333333"
        UNIBAS_GRAY2 = "#666666"
        UNIBAS_GRAY3 = "#777777"
        BTN_BG = UNIBAS_WHITE
        BTN_FG = UNIBAS_ANTHRAZIT
        BTN_DISABLED_BG = UNIBAS_GRAY5
        BTN_SELECTED_GREEN = UNIBAS_MINT
        BTN_OFF_RED = UNIBAS_RED
        BTN_AUTO_BLUE = UNIBAS_MINT
        BTN_PROMPT_ORANGE = UNIBAS_MINT_LIGHT
        BTN_SELECTED_FG = UNIBAS_ANTHRAZIT
        ACTION_BUTTON_BG = UNIBAS_ANTHRAZIT
        ACTION_BUTTON_FG = UNIBAS_WHITE
        ACTION_BUTTON_ACTIVE_BG = UNIBAS_ANTHRAZIT_LIGHT
        ACTION_BUTTON_ACTIVE_FG = UNIBAS_WHITE
        PRIMARY_BUTTON_BG = UNIBAS_RED
        PRIMARY_BUTTON_FG = UNIBAS_WHITE
        PRIMARY_BUTTON_ACTIVE_BG = "#B0042E"
        PRIMARY_BUTTON_ACTIVE_FG = UNIBAS_WHITE
        GHOST_BUTTON_BG = UNIBAS_WHITE
        GHOST_BUTTON_FG = UNIBAS_GRAY1
        GHOST_BUTTON_ACTIVE_BG = UNIBAS_MINT_LIGHT
        GHOST_BUTTON_ACTIVE_FG = UNIBAS_ANTHRAZIT
    GRAPH_COLORS = [UNIBAS_RED, UNIBAS_MINT, UNIBAS_ANTHRAZIT_LIGHT, UNIBAS_GRAY2, "#8A6F00"]

    # Selected theme from config/options. If the folder contains exactly one theme file,
    # resolve_theme_file() uses it automatically.
    selected = resolve_theme_file(theme_file or load_saved_theme_file())
    ACTIVE_THEME_FILE = selected.name if selected else ""
    if selected:
        try:
            data = json.loads(selected.read_text(encoding="utf-8"))
            mode = "dark" if dark_mode else "light"
            UNIBAS_MINT = _theme_color(data, mode, "mint", UNIBAS_MINT)
            UNIBAS_MINT_LIGHT = _theme_color(data, mode, "mint_light", UNIBAS_MINT_LIGHT)
            UNIBAS_RED = _theme_color(data, mode, "red", UNIBAS_RED, "danger", "off_bg")
            UNIBAS_ANTHRAZIT = _theme_color(data, mode, "anthrazit", UNIBAS_ANTHRAZIT, "header_bg")
            UNIBAS_ANTHRAZIT_LIGHT = _theme_color(data, mode, "anthrazit_light", UNIBAS_ANTHRAZIT_LIGHT, "header_active_bg")
            UNIBAS_BLACK = _theme_color(data, mode, "black", UNIBAS_BLACK)
            UNIBAS_WHITE = _theme_color(data, mode, "white", UNIBAS_WHITE, "card_bg", "canvas_bg")
            UNIBAS_GRAY1 = _theme_color(data, mode, "gray1", UNIBAS_GRAY1, "text", "text_fg")
            UNIBAS_GRAY2 = _theme_color(data, mode, "gray2", UNIBAS_GRAY2, "muted_text", "axis")
            UNIBAS_GRAY3 = _theme_color(data, mode, "gray3", UNIBAS_GRAY3, "disabled_text")
            UNIBAS_GRAY4 = _theme_color(data, mode, "gray4", UNIBAS_GRAY4)
            UNIBAS_GRAY5 = _theme_color(data, mode, "gray5", UNIBAS_GRAY5, "border")
            UNIBAS_GRAY6 = _theme_color(data, mode, "gray6", UNIBAS_GRAY6, "window_bg", "background")
            BTN_BG = _theme_color(data, mode, "button_bg", BTN_BG)
            BTN_FG = _theme_color(data, mode, "button_fg", BTN_FG)
            BTN_DISABLED_BG = _theme_color(data, mode, "button_disabled_bg", BTN_DISABLED_BG)
            BTN_SELECTED_GREEN = _theme_color(data, mode, "button_selected_bg", BTN_SELECTED_GREEN)
            BTN_OFF_RED = _theme_color(data, mode, "button_off_bg", BTN_OFF_RED)
            BTN_AUTO_BLUE = _theme_color(data, mode, "button_auto_bg", BTN_AUTO_BLUE)
            BTN_PROMPT_ORANGE = _theme_color(data, mode, "button_prompt_bg", BTN_PROMPT_ORANGE)
            BTN_SELECTED_FG = _theme_color(data, mode, "button_selected_fg", BTN_SELECTED_FG)

            ACTION_BUTTON_BG = _theme_color(data, mode, "action_button_bg", ACTION_BUTTON_BG, "secondary_button_bg")
            ACTION_BUTTON_FG = _theme_color(data, mode, "action_button_fg", ACTION_BUTTON_FG, "secondary_button_fg")
            ACTION_BUTTON_ACTIVE_BG = _theme_color(data, mode, "action_button_active_bg", ACTION_BUTTON_ACTIVE_BG, "secondary_button_active_bg")
            ACTION_BUTTON_ACTIVE_FG = _theme_color(data, mode, "action_button_active_fg", ACTION_BUTTON_ACTIVE_FG, "secondary_button_active_fg")
            PRIMARY_BUTTON_BG = _theme_color(data, mode, "primary_button_bg", PRIMARY_BUTTON_BG)
            PRIMARY_BUTTON_FG = _theme_color(data, mode, "primary_button_fg", PRIMARY_BUTTON_FG)
            PRIMARY_BUTTON_ACTIVE_BG = _theme_color(data, mode, "primary_button_active_bg", PRIMARY_BUTTON_ACTIVE_BG)
            PRIMARY_BUTTON_ACTIVE_FG = _theme_color(data, mode, "primary_button_active_fg", PRIMARY_BUTTON_ACTIVE_FG)
            GHOST_BUTTON_BG = _theme_color(data, mode, "ghost_button_bg", GHOST_BUTTON_BG)
            GHOST_BUTTON_FG = _theme_color(data, mode, "ghost_button_fg", GHOST_BUTTON_FG)
            GHOST_BUTTON_ACTIVE_BG = _theme_color(data, mode, "ghost_button_active_bg", GHOST_BUTTON_ACTIVE_BG)
            GHOST_BUTTON_ACTIVE_FG = _theme_color(data, mode, "ghost_button_active_fg", GHOST_BUTTON_ACTIVE_FG)
            section = data.get(mode, {}) if isinstance(data.get(mode, {}), dict) else {}
            colors = section.get("colors", section) if isinstance(section.get("colors", section), dict) else {}
            graph_colors = colors.get("graph_colors") or data.get("graph_colors")
            if isinstance(graph_colors, list) and graph_colors:
                GRAPH_COLORS = [str(c) for c in graph_colors if isinstance(c, str) and c.strip()]
            else:
                GRAPH_COLORS = [UNIBAS_RED, UNIBAS_MINT, UNIBAS_ANTHRAZIT_LIGHT, UNIBAS_GRAY2, "#8A6F00"]
            FONT_H1 = _theme_font(data, "h1", FONT_H1)
            FONT_H2 = _theme_font(data, "h2", FONT_H2)
            FONT_H5 = _theme_font(data, "h5", FONT_H5)
            FONT_H6 = _theme_font(data, "h6", FONT_H6)
            FONT_BODY = _theme_font(data, "body", FONT_BODY)
            FONT_BUTTON = _theme_font(data, "button", FONT_BUTTON)
            FONT_CAPTION = _theme_font(data, "caption", FONT_CAPTION)
        except Exception:
            ACTIVE_THEME_FILE = ""


def setup_unibas_theme(root: tk.Tk) -> None:
    """Apply the current theme."""
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        root.configure(background=UNIBAS_GRAY6)
        root.option_add("*Font", FONT_BODY)
        root.option_add("*Background", UNIBAS_GRAY6)
        root.option_add("*Foreground", UNIBAS_GRAY1)

        style.configure(".", font=FONT_BODY, foreground=UNIBAS_GRAY1, background=UNIBAS_GRAY6)
        style.configure("TFrame", background=UNIBAS_GRAY6)
        style.configure("Card.TFrame", background=UNIBAS_WHITE)
        style.configure("UniHeader.TFrame", background=UNIBAS_ANTHRAZIT)
        style.configure("MintBar.TFrame", background=UNIBAS_MINT)
        style.configure("TLabel", font=FONT_BODY, background=UNIBAS_GRAY6, foreground=UNIBAS_GRAY1)
        style.configure("Card.TLabel", font=FONT_BODY, background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1)
        style.configure("Caption.TLabel", font=FONT_CAPTION, background=UNIBAS_GRAY6, foreground=UNIBAS_GRAY2)
        style.configure("CardCaption.TLabel", font=FONT_CAPTION, background=UNIBAS_WHITE, foreground=UNIBAS_GRAY2)
        style.configure("Header.TLabel", font=FONT_H1, background=UNIBAS_ANTHRAZIT, foreground=UNIBAS_WHITE)
        style.configure("HeaderSub.TLabel", font=FONT_CAPTION, background=UNIBAS_ANTHRAZIT, foreground=UNIBAS_MINT_LIGHT)
        style.configure("Banner.TLabel", font=FONT_BODY, background=UNIBAS_MINT_LIGHT, foreground=UNIBAS_ANTHRAZIT, padding=(12, 8))

        style.configure("TLabelframe", background=UNIBAS_WHITE, bordercolor=UNIBAS_GRAY5, relief="solid")
        style.configure("TLabelframe.Label", background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, font=FONT_H5)
        style.configure("Card.TLabelframe", background=UNIBAS_WHITE, bordercolor=UNIBAS_GRAY5, relief="solid")
        style.configure("Card.TLabelframe.Label", background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, font=FONT_H5)

        disabled_fg = "#D2EBE9" if CURRENT_DARK_MODE else UNIBAS_GRAY3

        style.configure("TButton", font=FONT_BUTTON, padding=(9, 5), borderwidth=0, relief="flat",
                        background=ACTION_BUTTON_BG, foreground=ACTION_BUTTON_FG)
        style.map("TButton",
                  background=[("active", ACTION_BUTTON_ACTIVE_BG), ("pressed", UNIBAS_BLACK), ("disabled", UNIBAS_GRAY5)],
                  foreground=[("active", ACTION_BUTTON_ACTIVE_FG), ("pressed", ACTION_BUTTON_ACTIVE_FG), ("disabled", disabled_fg)])
        style.configure("Primary.TButton", background=PRIMARY_BUTTON_BG, foreground=PRIMARY_BUTTON_FG)
        style.map("Primary.TButton",
                  background=[("active", PRIMARY_BUTTON_ACTIVE_BG), ("pressed", "#8F0325"), ("disabled", UNIBAS_GRAY5)],
                  foreground=[("active", PRIMARY_BUTTON_ACTIVE_FG), ("pressed", PRIMARY_BUTTON_ACTIVE_FG), ("disabled", disabled_fg)])
        style.configure("Secondary.TButton", background=ACTION_BUTTON_BG, foreground=ACTION_BUTTON_FG)
        style.map("Secondary.TButton",
                  background=[("active", ACTION_BUTTON_ACTIVE_BG), ("pressed", UNIBAS_BLACK), ("disabled", UNIBAS_GRAY5)],
                  foreground=[("active", ACTION_BUTTON_ACTIVE_FG), ("pressed", ACTION_BUTTON_ACTIVE_FG), ("disabled", disabled_fg)])
        style.configure("Ghost.TButton", background=GHOST_BUTTON_BG, foreground=GHOST_BUTTON_FG, borderwidth=1, relief="solid")
        style.map("Ghost.TButton",
                  background=[("active", GHOST_BUTTON_ACTIVE_BG), ("pressed", UNIBAS_MINT), ("disabled", UNIBAS_GRAY5)],
                  foreground=[("active", GHOST_BUTTON_ACTIVE_FG), ("pressed", GHOST_BUTTON_ACTIVE_FG), ("disabled", disabled_fg)])

        style.configure("TEntry", fieldbackground=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, insertcolor=UNIBAS_GRAY1, padding=(5, 3))
        style.configure("TCombobox", fieldbackground=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, padding=(5, 3))
        style.map("TCombobox", fieldbackground=[("readonly", UNIBAS_WHITE)], foreground=[("readonly", UNIBAS_GRAY1)])
        style.configure("TCheckbutton", background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, font=FONT_BODY)
        style.configure("TRadiobutton", background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, font=FONT_BODY)
        style.configure("Vertical.TScrollbar", background=UNIBAS_GRAY5, troughcolor=UNIBAS_GRAY6, arrowcolor=UNIBAS_GRAY1)
        style.configure("Horizontal.TScrollbar", background=UNIBAS_GRAY5, troughcolor=UNIBAS_GRAY6, arrowcolor=UNIBAS_GRAY1)
    except Exception:
        pass


def _readable_fg_for_bg(bg: str) -> str:
    """Return a readable foreground colour for a hex/named background."""
    try:
        named = {
            "white": "#FFFFFF", "black": "#000000", "red": "#FF0000",
            "gray": "#808080", "grey": "#808080", "lightgray": "#D3D3D3",
            "lightgrey": "#D3D3D3", "darkgray": "#404040", "darkgrey": "#404040",
        }
        raw = str(bg).strip()
        raw = named.get(raw.lower(), raw)
        if not raw.startswith("#"):
            return UNIBAS_GRAY1
        color = raw.lstrip("#")
        if len(color) == 3:
            color = "".join(ch * 2 for ch in color)
        r = int(color[0:2], 16) / 255.0
        g = int(color[2:4], 16) / 255.0
        b = int(color[4:6], 16) / 255.0
        def lin(c):
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
        return "#FFFFFF" if lum < 0.45 else UNIBAS_ANTHRAZIT
    except Exception:
        return UNIBAS_GRAY1


def apply_action_button_style(button: tk.Button, kind: str = "secondary") -> None:
    """Style normal tk.Buttons so text stays readable in light and dark mode."""
    if kind == "primary":
        bg = UNIBAS_RED
        active_bg = "#B0042E"
    elif kind == "ghost":
        bg = UNIBAS_WHITE
        active_bg = UNIBAS_MINT_LIGHT
    else:
        bg = UNIBAS_ANTHRAZIT
        active_bg = UNIBAS_ANTHRAZIT_LIGHT
    fg = _readable_fg_for_bg(bg)
    active_fg = _readable_fg_for_bg(active_bg)
    button.configure(
        bg=bg,
        fg=fg,
        activebackground=active_bg,
        activeforeground=active_fg,
        disabledforeground=("#D2EBE9" if CURRENT_DARK_MODE else UNIBAS_GRAY3),
        highlightthickness=0,
        relief=tk.FLAT,
        borderwidth=0,
        padx=10,
        pady=6,
        font=FONT_BUTTON,
        cursor="hand2",
    )


def action_button(parent, text: str, command, kind: str = "secondary") -> tk.Button:
    button = tk.Button(parent, text=text, command=command)
    setattr(button, "_ngc3_action_kind", kind)
    apply_action_button_style(button, kind)
    return button


class ColorButton(tk.Frame):
    """A small button drawn with Frame+Label so bg colours work on macOS/Windows/Linux.

    Native tk.Button backgrounds are ignored by some macOS themes.  The ion-gauge
    buttons must show safety colours reliably, especially the red OFF button, so
    this widget implements the tiny subset of Button.configure() used below.
    """

    def __init__(self, parent, text: str, command=None, **kwargs):
        super().__init__(parent, borderwidth=0, highlightthickness=0)
        self._text = text
        self._command = command
        self._state = tk.NORMAL
        self._bg = kwargs.pop("bg", BTN_BG)
        self._fg = kwargs.pop("fg", _readable_fg_for_bg(self._bg))
        self._activebg = kwargs.pop("activebackground", self._bg)
        self._activefg = kwargs.pop("activeforeground", self._fg)
        self._disabledfg = kwargs.pop("disabledforeground", UNIBAS_GRAY3)
        self._font = kwargs.pop("font", FONT_BUTTON)
        self._padx = int(kwargs.pop("padx", 10))
        self._pady = int(kwargs.pop("pady", 8))
        self._cursor = kwargs.pop("cursor", "hand2")
        self.label = tk.Label(self, text=text, anchor="center", bd=0, highlightthickness=0)
        self.label.pack(fill="both", expand=True)
        self._bind_events(self)
        self._bind_events(self.label)
        self.configure(**kwargs)
        self._apply(resting=True)

    def _bind_events(self, widget):
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<Button-1>", self._on_click)

    def _apply(self, resting: bool = True):
        bg = self._bg if resting or self._state == tk.DISABLED else self._activebg
        fg = self._disabledfg if self._state == tk.DISABLED else (self._fg if resting else self._activefg)
        cursor = "" if self._state == tk.DISABLED else self._cursor
        super().configure(bg=bg, highlightbackground=bg, cursor=cursor)
        self.label.configure(
            bg=bg,
            fg=fg,
            activebackground=self._activebg,
            activeforeground=self._activefg,
            disabledforeground=self._disabledfg,
            font=self._font,
            padx=self._padx,
            pady=self._pady,
            cursor=cursor,
        )

    def _on_enter(self, _event=None):
        if self._state != tk.DISABLED:
            self._apply(resting=False)

    def _on_leave(self, _event=None):
        self._apply(resting=True)

    def _on_click(self, _event=None):
        if self._state != tk.DISABLED and callable(self._command):
            self._command()

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        if cnf:
            kwargs.update(cnf)
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            self.label.configure(text=self._text)
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "state" in kwargs:
            self._state = kwargs.pop("state")
        if "bg" in kwargs:
            self._bg = kwargs.pop("bg")
        if "background" in kwargs:
            self._bg = kwargs.pop("background")
        if "fg" in kwargs:
            self._fg = kwargs.pop("fg")
        if "foreground" in kwargs:
            self._fg = kwargs.pop("foreground")
        if "activebackground" in kwargs:
            self._activebg = kwargs.pop("activebackground")
        if "activeforeground" in kwargs:
            self._activefg = kwargs.pop("activeforeground")
        if "disabledforeground" in kwargs:
            self._disabledfg = kwargs.pop("disabledforeground")
        if "font" in kwargs:
            self._font = kwargs.pop("font")
        if "padx" in kwargs:
            self._padx = int(kwargs.pop("padx"))
        if "pady" in kwargs:
            self._pady = int(kwargs.pop("pady"))
        if "cursor" in kwargs:
            self._cursor = kwargs.pop("cursor")
        # Consume native Button options that are not meaningful for this drawn widget.
        for ignored in ("relief", "borderwidth", "bd", "highlightthickness", "highlightbackground"):
            kwargs.pop(ignored, None)
        if kwargs:
            super().configure(**kwargs)
        self._apply(resting=True)

    config = configure

    def cget(self, key):  # type: ignore[override]
        if key in ("bg", "background"):
            return self._bg
        if key in ("fg", "foreground"):
            return self._fg
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)


def normalize_recipients(value) -> List[str]:
    if isinstance(value, str):
        raw = re.split(r"[,;\n]+", value)
    else:
        raw = list(value or [])
    return [x.strip() for x in raw if str(x).strip()]


def safe_float(value) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def now_human(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or time.time()))


@dataclass
class GaugeRecord:
    key: str
    gauge_type: str
    gauge_number: str
    status_byte: int
    error_byte: int
    pressure_text: str
    units_code: str
    pressure_value: Optional[float] = None

    @property
    def units(self) -> str:
        return UNITS.get(self.units_code, self.units_code or "?")

    @property
    def pressure_display(self) -> str:
        if not self.pressure_text:
            return "---"
        return f"{self.pressure_text} {self.units}".strip()

    @property
    def is_emitting(self) -> bool:
        return self.gauge_type == "I" and bool(self.status_byte & (1 << 0))

    @property
    def pressure_mbar(self) -> Optional[float]:
        if self.pressure_value is None:
            return None
        if self.units == "mBar":
            return self.pressure_value
        if self.units == "Torr":
            return self.pressure_value * 1.333223684
        if self.units == "Pa":
            return self.pressure_value / 100.0
        return self.pressure_value


@dataclass
class NGC3Status:
    raw: bytes = b""
    ok: bool = False
    state_byte: int = 0
    error_byte: int = 0
    relay_byte: int = 0
    unused_byte: int = 0
    gauges: Dict[str, GaugeRecord] = field(default_factory=dict)
    temperature_c: Optional[int] = None
    timestamp: float = field(default_factory=time.time)
    parse_error: str = ""

    @property
    def remote_mode(self) -> bool:
        return bool(self.state_byte & (1 << 4))

    @property
    def selected_ig(self) -> str:
        return "IG2" if (self.state_byte & (1 << 6)) else "IG1"

    @property
    def any_ig_emitting(self) -> bool:
        return any(g.is_emitting for g in self.gauges.values())

    @property
    def relay_states(self) -> Dict[str, bool]:
        return {letter: bool(self.relay_byte & (1 << i)) for i, letter in enumerate("ABCD")}


def bits_to_labels(value: int, definitions: List[Tuple[int, str]], include_zero: bool = False) -> List[str]:
    labels = []
    for bit, label in definitions:
        is_set = bool(value & (1 << bit))
        if is_set:
            labels.append(label)
        elif include_zero:
            labels.append(f"not {label}")
    return labels


def format_bits(value: int, definitions: List[Tuple[int, str]]) -> str:
    labels = bits_to_labels(value, definitions)
    return ", ".join(labels) if labels else "none"


def parse_pressure(raw: bytes) -> Tuple[str, Optional[float]]:
    txt = raw.decode("ascii", errors="ignore").strip()
    txt = txt.rstrip(",").strip()
    if not txt:
        return "", None
    try:
        return txt, float(txt.replace(" ", ""))
    except ValueError:
        return txt, None


class NGC3Device:
    """Serial communication wrapper for one AML NGC3 controller."""

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUD):
        self.port = port
        self.baudrate = baudrate
        self.ser: Optional[serial.Serial] = None
        self.lock = threading.RLock()
        self.last_status: Optional[NGC3Status] = None
        # GUI-side state only: the NGC3 status report does not include the real emission
        # current value, only whether the ion gauge is in emission.
        self.last_emission_request = "off"  # off, 0.5mA, 5mA
        # 5 mA is enabled only after 0.5 mA has been requested in the current
        # emission cycle. It is reset when OFF or Release local is used.
        self.five_ma_unlocked = False
        # Software-side automatic emission switching. This is not the controller
        # internal Auto Em setup; it simply sends the same remote 0.5/5 mA commands
        # when the pressure crosses the documented thresholds.
        self.software_auto = False

    def open(self) -> None:
        with self.lock:
            if self.ser and self.ser.is_open:
                return
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=SERIAL_TIMEOUT_S,
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()

    def close(self) -> None:
        with self.lock:
            if self.ser:
                try:
                    self.ser.close()
                finally:
                    self.ser = None

    def _require_open(self) -> serial.Serial:
        if not self.ser or not self.ser.is_open:
            raise RuntimeError(f"Serial port {self.port} is not open.")
        return self.ser

    def send_command(self, command_char: str, parameter: str = "") -> None:
        """Send an NGC3 command: '*' + command_char + ignored byte '0' + optional parameter."""
        if len(command_char) != 1:
            raise ValueError("command_char must be exactly one character")
        if parameter and len(parameter) != 1:
            raise ValueError("parameter must be empty or exactly one character")
        packet = f"*{command_char}0{parameter}".encode("ascii")
        with self.lock:
            ser = self._require_open()
            ser.write(packet)
            ser.flush()
            # Most commands have no response. Give the NGC3 a short processing delay.
            time.sleep(0.12)

    def ensure_remote(self) -> None:
        # <control> C 0. This allows commands with parameters to be used.
        self.send_command("C")

    def release_local(self) -> None:
        # <release> R 0. NGC3 stops emission when returning to local control.
        self.send_command("R")
        self.last_emission_request = "off"
        self.five_ma_unlocked = False
        self.software_auto = False

    def reset_error(self) -> None:
        self.send_command("E")

    def _assert_safe_remote_takeover_for_emission_command(self) -> None:
        """Do not take remote control while a local emission is running."""
        st = self.last_status
        if st is not None and st.ok and st.any_ig_emitting and not st.remote_mode:
            raise RuntimeError(
                "Local emission is running. Remote command blocked."
            )

    def select_ig(self, ig_number: int) -> None:
        if ig_number not in (1, 2):
            raise ValueError("ig_number must be 1 or 2")
        self.ensure_remote()
        self.send_command("j", str(ig_number))

    def gauge_on_05ma(self, ig_number: int) -> None:
        self._assert_safe_remote_takeover_for_emission_command()
        self.select_ig(ig_number)
        # <Gauge on> i 0 n, n='0' = 0.5 mA.
        self.send_command("i", "0")
        self.last_emission_request = "0.5mA"
        self.five_ma_unlocked = True

    def gauge_on_5ma(self, ig_number: int) -> None:
        self._assert_safe_remote_takeover_for_emission_command()
        # GUI safety: do not allow direct 5 mA until 0.5 mA was tried
        # in the current emission cycle from this same controller panel.
        if not self.five_ma_unlocked:
            raise RuntimeError("Start at 0.5 mA first before enabling 5 mA.")
        self.select_ig(ig_number)
        # <Gauge on> i 0 n, n='1' = 5 mA.
        self.send_command("i", "1")
        self.last_emission_request = "5mA"

    def set_runtime_emission_state(self, state: str, emitting: bool) -> None:
        """Restore/update GUI-side current state from saved config or status polling."""
        if not emitting:
            self.last_emission_request = "off"
            self.five_ma_unlocked = False
            return
        if state in ("0.5mA", "5mA"):
            self.last_emission_request = state
            self.five_ma_unlocked = True
        elif self.last_emission_request not in ("0.5mA", "5mA"):
            # The NGC3 status report says that the ion gauge is in emission,
            # but it does not expose the emission current.  The controller starts
            # at 0.5 mA, so 0.5 mA is the safest fallback display state.
            self.last_emission_request = "0.5mA"
            self.five_ma_unlocked = True

    def gauge_off(self) -> None:
        self.ensure_remote()
        self.send_command("o")
        self.last_emission_request = "off"
        self.five_ma_unlocked = False
        self.software_auto = False

    def _read_until_idle(self) -> bytes:
        ser = self._require_open()
        buf = bytearray()
        deadline = time.monotonic() + STATUS_READ_TIMEOUT_S
        last_rx = time.monotonic()
        while time.monotonic() < deadline:
            chunk = ser.read(256)
            if chunk:
                buf.extend(chunk)
                last_rx = time.monotonic()
                continue
            if buf and (time.monotonic() - last_rx) >= STATUS_IDLE_DONE_S:
                break
        return bytes(buf)

    def read_status(self) -> NGC3Status:
        with self.lock:
            ser = self._require_open()
            ser.reset_input_buffer()
            ser.write(b"*S0")
            ser.flush()
            raw = self._read_until_idle()
        status = parse_status_report(raw)
        self.last_status = status
        return status


def parse_status_report(raw: bytes) -> NGC3Status:
    st = NGC3Status(raw=raw, timestamp=time.time())
    if not raw:
        st.parse_error = "No response"
        return st

    # The status report is binary at the beginning, followed by 'G' gauge records.
    # To be robust against stale bytes/echoes, locate the first gauge record and take
    # the four bytes before it as state, error, relay, unused.
    first_g = raw.find(b"G")
    if first_g >= 4:
        prefix_index = first_g - 4
    elif len(raw) >= 4:
        prefix_index = 0
        first_g = 4
    else:
        st.parse_error = f"Response too short: {raw!r}"
        return st

    try:
        st.state_byte = raw[prefix_index]
        st.error_byte = raw[prefix_index + 1]
        st.relay_byte = raw[prefix_index + 2]
        st.unused_byte = raw[prefix_index + 3]
    except IndexError:
        st.parse_error = f"Could not read header bytes: {raw!r}"
        return st

    i = first_g
    while i + 17 <= len(raw):
        if raw[i] != ord("G"):
            next_g = raw.find(b"G", i + 1)
            if next_g == -1:
                break
            i = next_g
            continue

        record = raw[i : i + 17]
        try:
            gauge_type = chr(record[1])
            gauge_number = chr(record[2])
            status_byte = record[3]
            error_byte = record[4]
            pressure_text, pressure_value = parse_pressure(record[5:13])
            units_code = chr(record[13]) if record[13] else ""
            key = GAUGE_NAME_BY_NUMBER.get(gauge_number, f"Gauge {gauge_number}")
            st.gauges[key] = GaugeRecord(
                key=key,
                gauge_type=gauge_type,
                gauge_number=gauge_number,
                status_byte=status_byte,
                error_byte=error_byte,
                pressure_text=pressure_text,
                pressure_value=pressure_value,
                units_code=units_code,
            )
        except Exception as exc:
            st.parse_error = f"Gauge record parse error at byte {i}: {exc}"
            break
        i += 17

    # Temperature is reported after gauge records as three bytes followed by 'C'.
    # It may include spaces, e.g. b' 25C\r\n'.
    tail = raw[i:]
    m = re.search(rb"([ \-\d]{1,3})C\r?\n", tail)
    if m:
        try:
            st.temperature_c = int(m.group(1).decode("ascii").strip())
        except ValueError:
            pass

    st.ok = bool(st.gauges)
    if not st.ok and not st.parse_error:
        st.parse_error = f"No gauge records found in response: {raw!r}"
    return st


@dataclass
class PfeifferGaugeRecord:
    key: str
    channel: int
    status_code: int
    pressure_value: Optional[float]
    units: str
    sensor_id: str = ""

    @property
    def pressure_text(self) -> str:
        if self.pressure_value is None:
            return ""
        return f"{self.pressure_value:.4e}"

    @property
    def pressure_display(self) -> str:
        status = PFEIFFER_STATUS_LABELS.get(self.status_code, f"Status {self.status_code}")
        if self.pressure_value is None:
            return status
        if self.status_code == 0:
            return f"{self.pressure_value:.4e} {self.units}"
        if self.status_code == 1:
            return f"< {self.pressure_value:.4e} {self.units}  ({status})"
        if self.status_code == 2:
            return f"> {self.pressure_value:.4e} {self.units}  ({status})"
        return status

    @property
    def pressure_mbar(self) -> Optional[float]:
        # Status 1/2 values are range limits rather than measured pressures and are
        # intentionally not logged as normal data points.
        if self.status_code != 0 or self.pressure_value is None:
            return None
        if self.units == "mBar":
            return self.pressure_value
        if self.units == "Torr":
            return self.pressure_value * 1.333223684
        if self.units == "Pa":
            return self.pressure_value / 100.0
        return self.pressure_value


@dataclass
class PfeifferStatus:
    raw: str = ""
    ok: bool = False
    gauges: Dict[str, PfeifferGaugeRecord] = field(default_factory=dict)
    units: str = "mBar"
    timestamp: float = field(default_factory=time.time)
    parse_error: str = ""


def parse_pfeiffer_prx_response(response: str, units: str, sensor_ids: Tuple[str, str]) -> PfeifferStatus:
    """Parse the TPG 262 PRX response: status1,value1,status2,value2."""
    status = PfeifferStatus(raw=response, units=units, timestamp=time.time())
    parts = [part.strip() for part in str(response).strip().split(",")]
    if len(parts) != 4:
        status.parse_error = f"Unexpected PRX response: {response!r}"
        return status
    try:
        status_1 = int(parts[0])
        value_1 = float(parts[1])
        status_2 = int(parts[2])
        value_2 = float(parts[3])
    except Exception as exc:
        status.parse_error = f"Could not parse PRX response {response!r}: {exc}"
        return status

    for channel, status_code, pressure_value, sensor_id in (
        (1, status_1, value_1, sensor_ids[0]),
        (2, status_2, value_2, sensor_ids[1]),
    ):
        key = f"Channel {channel}"
        status.gauges[key] = PfeifferGaugeRecord(
            key=key,
            channel=channel,
            status_code=status_code,
            pressure_value=pressure_value,
            units=units,
            sensor_id=sensor_id,
        )
    status.ok = True
    return status


class PfeifferDualGaugeDevice:
    """Read-only RS232 interface for a Pfeiffer DualGauge TPG 262.

    The TPG 261/262 protocol uses three-character mnemonics. A query is sent as
    ``MNEMONIC<CR>``, acknowledged with ``ACK<CR><LF>``, then the host sends ENQ
    and receives one ASCII data line. This class only reads pressure, units and
    transmitter identification; it never changes sensor or controller settings.
    """

    UNIT_BY_CODE = {0: "mBar", 1: "Torr", 2: "Pa"}

    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.ser: Optional[serial.Serial] = None
        self.lock = threading.RLock()
        self.unit_code: Optional[int] = None
        self.sensor_ids: Tuple[str, str] = ("", "")
        self._last_metadata_query = 0.0

    def open(self) -> None:
        with self.lock:
            if self.ser and self.ser.is_open:
                return
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.15,
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            # ETX clears the TPG input/interface state and also stops any stale
            # automatic measurement stream left from a previous session.
            self.ser.write(b"\x03")
            self.ser.flush()
            time.sleep(0.08)
            self.ser.reset_input_buffer()

    def close(self) -> None:
        with self.lock:
            if self.ser:
                try:
                    self.ser.close()
                finally:
                    self.ser = None

    def _require_open(self) -> serial.Serial:
        if not self.ser or not self.ser.is_open:
            raise RuntimeError(f"Serial port {self.port} is not open.")
        return self.ser

    def _read_line(self, timeout: float = 1.2) -> bytes:
        ser = self._require_open()
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = ser.read(1)
            if not chunk:
                continue
            buf.extend(chunk)
            if chunk == b"\n":
                return bytes(buf)
        raise TimeoutError("Timed out waiting for a Pfeiffer DualGauge response.")

    def _query(self, mnemonic: str) -> str:
        if not re.fullmatch(r"[A-Z0-9]{3}", mnemonic):
            raise ValueError(f"Invalid Pfeiffer mnemonic: {mnemonic!r}")
        with self.lock:
            ser = self._require_open()
            ser.reset_input_buffer()
            ser.write(mnemonic.encode("ascii") + b"\r")
            ser.flush()

            ack_deadline = time.monotonic() + 1.4
            acknowledged = False
            while time.monotonic() < ack_deadline:
                line = self._read_line(max(0.1, ack_deadline - time.monotonic()))
                if line.startswith(b"\x06"):
                    acknowledged = True
                    break
                if line.startswith(b"\x15"):
                    raise RuntimeError(f"Pfeiffer DualGauge rejected command {mnemonic} (NAK).")
                # Ignore a possible stale continuous-measurement line.
            if not acknowledged:
                raise TimeoutError(f"No ACK received for Pfeiffer command {mnemonic}.")

            ser.write(b"\x05")  # ENQ: request the selected data string.
            ser.flush()
            data_line = self._read_line(1.4)
            return data_line.decode("ascii", errors="replace").strip()

    def _refresh_metadata(self) -> None:
        unit_text = self._query("UNI")
        try:
            self.unit_code = int(unit_text.split(",", 1)[0].strip())
        except Exception as exc:
            raise RuntimeError(f"Unexpected UNI response: {unit_text!r}") from exc

        tid_text = self._query("TID")
        ids = [part.strip() for part in tid_text.split(",")]
        if len(ids) >= 2:
            self.sensor_ids = (ids[0], ids[1])
        elif len(ids) == 1:
            self.sensor_ids = (ids[0], "")
        self._last_metadata_query = time.monotonic()

    def read_status(self) -> PfeifferStatus:
        with self.lock:
            if self.unit_code is None or (time.monotonic() - self._last_metadata_query) >= 60.0:
                self._refresh_metadata()
            response = self._query("PRX")
            units = self.UNIT_BY_CODE.get(self.unit_code or 0, "mBar")
            return parse_pfeiffer_prx_response(response, units, self.sensor_ids)


README_FALLBACK = """# UHV Sentinel

README.md was not found next to uhv_sentinel.py.

Place README.md in the same folder as uhv_sentinel.py, then reopen this help window.

Manual: https://arunmicro.com/documentation/Manual_NGC3.pdf
"""


def project_file(filename: str) -> Path:
    """Return a file expected to sit next to this Python script."""
    return Path(__file__).resolve().parent / filename


def _strip_inline_markdown(text: str) -> str:
    """Remove simple inline Markdown markers for the built-in help viewer."""
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("`", "")
    return text


def _insert_markdown_inline(text_widget: tk.Text, text: str, base_tag: str = "body") -> None:
    """Insert one Markdown line with clickable links into a Tk Text widget."""
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)|(https?://[^\s)>\]\"]+)")
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            text_widget.insert("end", _strip_inline_markdown(text[pos:match.start()]), (base_tag,))

        label = match.group(1) if match.group(1) is not None else match.group(3)
        url = match.group(2) if match.group(2) is not None else match.group(3)
        tag = f"url_{len(text_widget.tag_names())}_{match.start()}"
        text_widget.insert("end", _strip_inline_markdown(label), (base_tag, "url", tag))
        text_widget.tag_bind(tag, "<Button-1>", lambda _event, link=url: webbrowser.open(link))
        pos = match.end()

    if pos < len(text):
        text_widget.insert("end", _strip_inline_markdown(text[pos:]), (base_tag,))


def render_markdown_help(text_widget: tk.Text, markdown_text: str) -> None:
    """Render a small GitHub-style README subset in the GUI.

    This avoids showing raw Markdown syntax while keeping the program dependency-free.
    Supported elements are headings, bullets, numbered lists, code blocks and links.
    """
    text_widget.tag_configure("h1", font=FONT_H2, foreground=UNIBAS_RED, spacing1=10, spacing3=8)
    text_widget.tag_configure("h2", font=FONT_H5, foreground=UNIBAS_GRAY1, spacing1=10, spacing3=5)
    text_widget.tag_configure("h3", font=FONT_H5, spacing1=8, spacing3=4)
    text_widget.tag_configure("body", font=FONT_BODY, spacing3=3)
    text_widget.tag_configure("bullet", font=FONT_BODY, lmargin1=22, lmargin2=38, spacing3=3)
    text_widget.tag_configure("code", font=("Menlo", 12), background=UNIBAS_GRAY6, lmargin1=16, lmargin2=16, spacing1=3, spacing3=3)
    text_widget.tag_configure("url", foreground=UNIBAS_RED, underline=True)
    text_widget.tag_bind("url", "<Enter>", lambda _event: text_widget.config(cursor="hand2"))
    text_widget.tag_bind("url", "<Leave>", lambda _event: text_widget.config(cursor=""))

    in_code_block = False
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()

        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            if not in_code_block:
                text_widget.insert("end", "\n", ("body",))
            continue

        if in_code_block:
            text_widget.insert("end", line + "\n", ("code",))
            continue

        if not line.strip():
            text_widget.insert("end", "\n", ("body",))
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            tag = "h1" if level == 1 else "h2" if level == 2 else "h3"
            _insert_markdown_inline(text_widget, heading.group(2), tag)
            text_widget.insert("end", "\n")
            continue

        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            text_widget.insert("end", "• ", ("bullet",))
            _insert_markdown_inline(text_widget, bullet.group(1), "bullet")
            text_widget.insert("end", "\n")
            continue

        numbered = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if numbered:
            text_widget.insert("end", f"{numbered.group(1)}. ", ("bullet",))
            _insert_markdown_inline(text_widget, numbered.group(2), "bullet")
            text_widget.insert("end", "\n")
            continue

        _insert_markdown_inline(text_widget, line, "body")
        text_widget.insert("end", "\n")


@dataclass
class AlertEvent:
    id: str
    key: str
    controller_name: str
    severity: str
    message: str
    details: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    count: int = 1
    active: bool = True
    email_sent_at: Optional[float] = None


@dataclass
class AlertSettings:
    alerts_enabled: bool = True
    email_enabled: bool = False
    recipients: List[str] = field(default_factory=list)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_username: str = ""
    smtp_sender: str = ""
    cooldown_seconds: int = 300
    no_response_seconds: int = 5
    ig_pressure_critical_mbar: str = "1e-5"
    pirani_pressure_warning_mbar: str = ""
    active_pressure_warning_mbar: str = ""
    pressure_rise_enabled: bool = True
    pressure_rise_factor: float = 10.0
    pressure_rise_window_seconds: int = 30
    ig_sensitivity_mbar_inv: str = str(DEFAULT_IG_SENSITIVITY_MBAR_INV)
    pressure_logging_enabled: bool = True
    pressure_log_interval_seconds: int = 600
    graph_default_range: str = "24 h"
    theme_file: str = ""
    graph_selected_ig_only: bool = True
    graph_visible_gauges: List[str] = field(default_factory=lambda: list(GRAPH_GAUGE_KEYS))
    remote_control_enabled: bool = False
    dark_mode: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alerts_enabled": self.alerts_enabled,
            "email_enabled": self.email_enabled,
            "recipients": self.recipients,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_use_tls": self.smtp_use_tls,
            "smtp_use_ssl": self.smtp_use_ssl,
            "smtp_username": self.smtp_username,
            "smtp_sender": self.smtp_sender,
            "cooldown_seconds": self.cooldown_seconds,
            "no_response_seconds": self.no_response_seconds,
            "ig_pressure_critical_mbar": self.ig_pressure_critical_mbar,
            "pirani_pressure_warning_mbar": self.pirani_pressure_warning_mbar,
            "active_pressure_warning_mbar": self.active_pressure_warning_mbar,
            "pressure_rise_enabled": self.pressure_rise_enabled,
            "pressure_rise_factor": self.pressure_rise_factor,
            "pressure_rise_window_seconds": self.pressure_rise_window_seconds,
            "ig_sensitivity_mbar_inv": self.ig_sensitivity_mbar_inv,
            "pressure_logging_enabled": self.pressure_logging_enabled,
            "pressure_log_interval_seconds": self.pressure_log_interval_seconds,
            "graph_default_range": self.graph_default_range,
            "theme_file": self.theme_file,
            "graph_selected_ig_only": self.graph_selected_ig_only,
            "graph_visible_gauges": self.graph_visible_gauges,
            "remote_control_enabled": self.remote_control_enabled,
            "dark_mode": self.dark_mode,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlertSettings":
        settings = cls()
        for key in settings.to_dict().keys():
            if key in data:
                setattr(settings, key, data[key])
        settings.recipients = normalize_recipients(settings.recipients)
        for name, default in [("smtp_port", 587), ("cooldown_seconds", 300), ("no_response_seconds", 5), ("pressure_rise_window_seconds", 30), ("pressure_log_interval_seconds", 600)]:
            try:
                setattr(settings, name, int(getattr(settings, name)))
            except Exception:
                setattr(settings, name, default)
        try:
            settings.pressure_rise_factor = float(settings.pressure_rise_factor)
        except Exception:
            settings.pressure_rise_factor = 10.0
        if settings.graph_default_range not in GRAPH_RANGE_OPTIONS:
            settings.graph_default_range = "24 h"
        resolved_theme = resolve_theme_file(str(getattr(settings, "theme_file", "") or ""))
        settings.theme_file = resolved_theme.name if resolved_theme else ""
        settings.graph_selected_ig_only = bool(settings.graph_selected_ig_only)
        if not isinstance(settings.graph_visible_gauges, list):
            settings.graph_visible_gauges = list(GRAPH_GAUGE_KEYS)
        settings.graph_visible_gauges = [g for g in settings.graph_visible_gauges if g in GRAPH_GAUGE_KEYS]
        if not settings.graph_visible_gauges:
            settings.graph_visible_gauges = list(GRAPH_GAUGE_KEYS)
        settings.pressure_log_interval_seconds = max(60, int(settings.pressure_log_interval_seconds or 600))
        return settings


SMTP_PASSWORD_RUNTIME = ""


def get_smtp_password() -> str:
    return SMTP_PASSWORD_RUNTIME or os.environ.get(SMTP_PASSWORD_ENV, "") or os.environ.get("NGC3_SMTP_PASSWORD", "")


def set_runtime_smtp_password(password: str) -> None:
    global SMTP_PASSWORD_RUNTIME
    if password:
        SMTP_PASSWORD_RUNTIME = password


def emission_thresholds_mbar(sensitivity: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """Return (increase_to_5mA_threshold, decrease_to_0.5mA_threshold)."""
    if sensitivity is None or sensitivity <= 0:
        return None, None
    increase = 100e-12 / (sensitivity * 0.5e-3)
    decrease = increase * (10 ** 0.5)
    return increase, decrease


class AlertManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings = AlertSettings()
        self.active: Dict[str, AlertEvent] = {}
        self.history: List[AlertEvent] = []
        self.last_email_for_key: Dict[str, float] = {}
        self._email_inflight: set[str] = set()

    def configure(self, settings: AlertSettings) -> None:
        with self.lock:
            self.settings = settings

    def clear(self) -> None:
        with self.lock:
            self.active.clear()
            self.history.clear()
            self.last_email_for_key.clear()
            self._email_inflight.clear()

    def update_panel_alerts(self, panel: "DevicePanel", candidates: List[Tuple[str, str, str, str]]) -> None:
        if not self.settings.alerts_enabled:
            return
        current = set()
        for suffix, severity, message, details in candidates:
            key = f"{panel.port}:{suffix}"
            current.add(key)
            self.raise_alert(panel.display_name, key, severity, message, details)
        with self.lock:
            for key, alert in list(self.active.items()):
                if key.startswith(f"{panel.port}:") and key not in current:
                    alert.active = False
                    alert.last_seen = time.time()
                    self.history.append(alert)
                    del self.active[key]
            self.history = self.history[-ALERT_HISTORY_LIMIT:]

    def raise_alert(self, controller_name: str, key: str, severity: str, message: str, details: str = "") -> None:
        now = time.time()
        with self.lock:
            alert = self.active.get(key)
            if alert is None:
                alert = AlertEvent(str(uuid.uuid4()), key, controller_name, severity, message, details, now, now, 1, True)
                self.active[key] = alert
            else:
                alert.controller_name = controller_name
                alert.severity = severity
                alert.message = message
                alert.details = details
                alert.last_seen = now
                alert.count += 1
        self.maybe_send_email(alert)

    def maybe_send_email(self, alert: AlertEvent) -> None:
        """Send alert mail outside the Tk thread so SMTP cannot freeze the GUI."""
        settings = self.settings
        # Only critical alerts send emails. Warnings and infos stay in the GUI/history.
        if not settings.email_enabled or alert.severity != "critical" or not settings.recipients:
            return
        now = time.time()
        cooldown = max(30, int(settings.cooldown_seconds))
        with self.lock:
            last = self.last_email_for_key.get(alert.key, 0.0)
            if now - last < cooldown or alert.key in self._email_inflight:
                return
            # Reserve the cooldown immediately. Otherwise a failing SMTP connection
            # could start one new worker on every pressure poll.
            self.last_email_for_key[alert.key] = now
            self._email_inflight.add(alert.key)

        def worker() -> None:
            try:
                self.send_email(alert)
                with self.lock:
                    alert.email_sent_at = time.time()
            except Exception as exc:
                failed_at = time.time()
                with self.lock:
                    fail_key = f"email_failure:{alert.key}"
                    existing = self.active.get(fail_key)
                    if existing is None:
                        self.active[fail_key] = AlertEvent(
                            str(uuid.uuid4()), fail_key, alert.controller_name, "warning",
                            "Email alert could not be sent", str(exc), failed_at, failed_at, 1, True
                        )
                    else:
                        existing.details = str(exc)
                        existing.last_seen = failed_at
                        existing.count += 1
            finally:
                with self.lock:
                    self._email_inflight.discard(alert.key)

        threading.Thread(target=worker, name=f"UHV-email-{alert.key}", daemon=True).start()

    def send_email(self, alert: AlertEvent) -> None:
        settings = self.settings
        if not settings.smtp_host:
            raise RuntimeError("SMTP host is empty")
        sender = settings.smtp_sender or settings.smtp_username
        if not sender:
            raise RuntimeError("SMTP sender is empty")
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = ", ".join(settings.recipients)
        msg["Subject"] = f"[UHV Sentinel {alert.severity.upper()}] {alert.controller_name}: {alert.message}"
        msg.set_content(
            "UHV Sentinel laboratory monitor alert\n\n"
            f"Severity: {alert.severity}\n"
            f"Controller: {alert.controller_name}\n"
            f"Message: {alert.message}\n"
            f"Details: {alert.details}\n"
            f"Time: {now_human(alert.last_seen)}\n"
            f"Host: {platform.node()}\n\n"
            "UHV Sentinel monitor.\n"
        )
        password = get_smtp_password()
        context = ssl.create_default_context()
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context) as smtp:
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                if settings.smtp_use_tls:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, password)
                smtp.send_message(msg)

    def send_test_email(self) -> None:
        alert = AlertEvent(
            id=str(uuid.uuid4()), key="test_email", controller_name="Test", severity="critical",
            message="Test email from UHV Sentinel",
            details="If you receive this message, the SMTP configuration works."
        )
        self.send_email(alert)

    def active_alerts(self) -> List[AlertEvent]:
        with self.lock:
            return sorted(self.active.values(), key=lambda a: (SEVERITY_ORDER.get(a.severity, 0), a.last_seen), reverse=True)

    def recent_history(self) -> List[AlertEvent]:
        with self.lock:
            return list(reversed(self.history[-ALERT_HISTORY_LIMIT:]))


alert_manager = AlertManager()


class PressureLogger:
    """Weekly SQLite pressure logger used by the plotter.

    New pressure data is stored in one SQLite file per ISO week, for example
    pressure_history_2026-W27.sqlite3.  The plotter reads all weekly files needed
    for the selected range, so the All / 48 h / 24 h / 1 h graph options keep
    working even when the display crosses a week boundary.  The old single
    pressure_history.sqlite3 file is still read if present, for backward
    compatibility.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self._ready_paths = set()
        self._series_cache: Dict[Tuple[str, Optional[int]], Tuple[float, Dict[str, List[Tuple[float, float]]]]] = {}

    def _week_key_for_ts(self, ts: float) -> str:
        try:
            iso_year, iso_week, _iso_day = dt.datetime.fromtimestamp(float(ts)).date().isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        except Exception:
            return time.strftime("%Y-W%W", time.localtime(ts))

    def _path_for_ts(self, ts: float) -> Path:
        return self.path.with_name(f"{self.path.stem}_{self._week_key_for_ts(ts)}{self.path.suffix}")

    def data_location_text(self) -> str:
        return str(self.path.with_name(f"{self.path.stem}_YYYY-Www{self.path.suffix}"))

    def _weekly_paths(self) -> List[Path]:
        try:
            paths = sorted(self.path.parent.glob(f"{self.path.stem}_*-W*{self.path.suffix}"))
        except Exception:
            paths = []
        return [path for path in paths if path.is_file()]

    def _candidate_paths(self) -> List[Path]:
        # Keep the legacy single database readable.  If the same rows have already
        # been split into weekly files, query_series() de-duplicates them.
        paths = self._weekly_paths()
        if self.path.exists():
            paths.append(self.path)
        unique: Dict[str, Path] = {}
        for path in paths:
            unique[str(path)] = path
        return sorted(unique.values(), key=lambda p: p.name)

    def _ensure_db(self, path: Path) -> None:
        path_key = str(path)
        if path_key in self._ready_paths:
            return
        with self.lock:
            if path_key in self._ready_paths:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pressure_log (
                        ts REAL NOT NULL,
                        local_datetime TEXT,
                        local_date TEXT,
                        controller_name TEXT NOT NULL,
                        port TEXT NOT NULL,
                        gauge TEXT NOT NULL,
                        pressure_mbar REAL NOT NULL,
                        pressure_text TEXT,
                        units TEXT
                    )
                    """
                )
                columns = {row[1] for row in db.execute("PRAGMA table_info(pressure_log)")}
                if "local_datetime" not in columns:
                    db.execute("ALTER TABLE pressure_log ADD COLUMN local_datetime TEXT")
                if "local_date" not in columns:
                    db.execute("ALTER TABLE pressure_log ADD COLUMN local_date TEXT")
                db.execute("CREATE INDEX IF NOT EXISTS idx_pressure_log_port_ts ON pressure_log(port, ts)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_pressure_log_port_gauge_ts ON pressure_log(port, gauge, ts)")
                db.commit()
            self._ready_paths.add(path_key)

    def maybe_log_status(self, panel: Any, status: Any) -> None:
        settings = alert_manager.settings
        if not settings.pressure_logging_enabled or not status.ok:
            return
        interval = max(60, int(settings.pressure_log_interval_seconds or 600))
        now = time.time()
        if now - panel.last_logged_at < interval:
            return
        local_datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        local_date = time.strftime("%Y-%m-%d", time.localtime(now))
        rows = []
        for key, gauge in status.gauges.items():
            p_mbar = gauge.pressure_mbar
            if p_mbar is None or p_mbar <= 0:
                continue
            rows.append((now, local_datetime, local_date, panel.display_name, panel.port, key, float(p_mbar), gauge.pressure_text, gauge.units))
        if not rows:
            return
        db_path = self._path_for_ts(now)
        self._ensure_db(db_path)
        with self.lock:
            with sqlite3.connect(db_path) as db:
                db.executemany(
                    "INSERT INTO pressure_log(ts, local_datetime, local_date, controller_name, port, gauge, pressure_mbar, pressure_text, units) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                db.commit()
        panel.last_logged_at = now
        with self.lock:
            for cache_key in [key for key in self._series_cache if key[0] == panel.port]:
                self._series_cache.pop(cache_key, None)
        minutes = interval / 60.0
        if minutes.is_integer():
            interval_text = f"{int(minutes)} min"
        else:
            interval_text = f"{interval} s"
        panel.log_status_var.set(f"Last log: {now_human(now)}  ({len(rows)} channels, every {interval_text}, {db_path.name})")

    def query_series(self, port: str, range_seconds: Optional[int]) -> Dict[str, List[Tuple[float, float]]]:
        cache_key = (port, range_seconds)
        now_mono = time.monotonic()
        with self.lock:
            cached = self._series_cache.get(cache_key)
            if cached is not None and now_mono - cached[0] <= GRAPH_QUERY_CACHE_S:
                return {gauge: list(values) for gauge, values in cached[1].items()}

        result: Dict[str, List[Tuple[float, float]]] = {}
        params: Tuple[Any, ...]
        if range_seconds is None:
            query = "SELECT gauge, ts, pressure_mbar FROM pressure_log WHERE port=? ORDER BY ts ASC"
            params = (port,)
        else:
            query = "SELECT gauge, ts, pressure_mbar FROM pressure_log WHERE port=? AND ts>=? ORDER BY ts ASC"
            params = (port, time.time() - range_seconds)
        seen = set()
        with self.lock:
            for db_path in self._candidate_paths():
                try:
                    with sqlite3.connect(db_path) as db:
                        for gauge, ts, pressure in db.execute(query, params):
                            if pressure is None or pressure <= 0:
                                continue
                            gauge_name = str(gauge)
                            ts_value = float(ts)
                            pressure_value = float(pressure)
                            dedupe_key = (gauge_name, ts_value, pressure_value)
                            if dedupe_key in seen:
                                continue
                            seen.add(dedupe_key)
                            result.setdefault(gauge_name, []).append((ts_value, pressure_value))
                except sqlite3.Error:
                    continue
        for values in result.values():
            values.sort(key=lambda item: item[0])
        with self.lock:
            self._series_cache[cache_key] = (now_mono, {gauge: list(values) for gauge, values in result.items()})
        return result


pressure_logger = PressureLogger(PRESSURE_DB_PATH)


class _LocalThreadingHTTPServer(ThreadingHTTPServer):
    """Small read-only HTTP server with daemon request threads."""

    daemon_threads = True
    allow_reuse_address = True


class LocalPressureWebServer:
    """Read-only pressure dashboard on loopback and private LAN interfaces only.

    It deliberately does not bind to public IPv4 addresses, does not implement any
    control endpoint, and does not use UPnP or router configuration.  The browser
    page and JSON API are therefore available on this computer and, when a private
    LAN address exists, to computers on that same private network.
    """

    def __init__(self, port_start: int = WEB_MONITOR_PORT_START) -> None:
        self.port_start = int(port_start)
        self.port: Optional[int] = None
        self.lock = threading.RLock()
        self._panels: Dict[str, Dict[str, Any]] = {}
        self._alerts: List[Dict[str, Any]] = []
        self._servers: List[_LocalThreadingHTTPServer] = []
        self._threads: List[threading.Thread] = []
        self.bound_addresses: List[str] = []
        self.start_error = ""

    @staticmethod
    def _private_ipv4_addresses() -> List[str]:
        found = set()
        names = {socket.gethostname(), socket.getfqdn()}
        for name in names:
            try:
                for item in socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM):
                    found.add(item[4][0])
            except OSError:
                pass
        # Determine addresses selected by the OS routing table. UDP connect does
        # not send application data and works even when the target is unreachable.
        for target in ("192.0.2.1", "198.51.100.1"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((target, 9))
                found.add(sock.getsockname()[0])
            except OSError:
                pass
            finally:
                sock.close()
        result = []
        for text in found:
            try:
                address = ipaddress.ip_address(text)
            except ValueError:
                continue
            if address.version == 4 and not address.is_loopback and (address.is_private or address.is_link_local):
                result.append(text)
        return sorted(set(result), key=lambda value: tuple(int(part) for part in value.split(".")))

    def _handler_class(self):
        monitor = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "UHVSentinel/0.1"
            protocol_version = "HTTP/1.0"

            def log_message(self, _format: str, *_args) -> None:
                return

            def _request_is_local(self) -> bool:
                try:
                    client = ipaddress.ip_address(self.client_address[0])
                    return bool(client.is_loopback or client.is_private or client.is_link_local)
                except ValueError:
                    return False

            def _send(self, status: int, content_type: str, payload: bytes, *, head_only: bool = False) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Connection", "close")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
                self.end_headers()
                self.close_connection = True
                if not head_only:
                    try:
                        self.wfile.write(payload)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            def _handle(self, head_only: bool = False) -> None:
                if not self._request_is_local():
                    self._send(403, "text/plain; charset=utf-8", b"Local network access only.\n", head_only=head_only)
                    return
                request = urlsplit(self.path)
                path = request.path
                if path in ("/", "/index.html"):
                    payload = monitor._dashboard_html().encode("utf-8")
                    self._send(200, "text/html; charset=utf-8", payload, head_only=head_only)
                elif path == "/api/status":
                    payload = json.dumps(monitor.snapshot(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", payload, head_only=head_only)
                elif path == "/api/history":
                    query = parse_qs(request.query, keep_blank_values=False)
                    port = str((query.get("port") or [""])[0]).strip()
                    range_name = str((query.get("range") or ["24h"])[0]).strip().lower()
                    if not port:
                        self._send(400, "application/json; charset=utf-8", b'{"error":"Missing port"}', head_only=head_only)
                        return
                    try:
                        history = monitor.history_snapshot(port, range_name)
                    except KeyError:
                        self._send(404, "application/json; charset=utf-8", b'{"error":"Unknown pressure panel"}', head_only=head_only)
                        return
                    except Exception as exc:
                        payload = json.dumps({"error": f"Pressure history unavailable: {exc}"}, ensure_ascii=False).encode("utf-8")
                        self._send(500, "application/json; charset=utf-8", payload, head_only=head_only)
                        return
                    payload = json.dumps(history, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", payload, head_only=head_only)
                elif path == "/health":
                    self._send(200, "text/plain; charset=utf-8", b"ok\n", head_only=head_only)
                elif path == "/favicon.ico":
                    self._send(204, "image/x-icon", b"", head_only=head_only)
                else:
                    self._send(404, "text/plain; charset=utf-8", b"Not found.\n", head_only=head_only)

            def do_GET(self) -> None:
                self._handle(False)

            def do_HEAD(self) -> None:
                self._handle(True)

            def do_POST(self) -> None:
                self._send(405, "text/plain; charset=utf-8", b"Read-only monitor.\n")

        return Handler

    def start(self) -> None:
        if self._servers:
            return
        addresses = ["127.0.0.1"] + self._private_ipv4_addresses()
        handler = self._handler_class()
        self.start_error = ""
        for candidate_port in range(self.port_start, self.port_start + WEB_MONITOR_PORT_ATTEMPTS):
            try:
                loopback_server = _LocalThreadingHTTPServer(("127.0.0.1", candidate_port), handler)
            except OSError as exc:
                self.start_error = str(exc)
                continue
            servers = [loopback_server]
            bound = ["127.0.0.1"]
            for address in addresses[1:]:
                try:
                    servers.append(_LocalThreadingHTTPServer((address, candidate_port), handler))
                    bound.append(address)
                except OSError:
                    # A stale or otherwise unavailable interface must not prevent
                    # localhost access from working.
                    continue
            self.port = candidate_port
            self._servers = servers
            self.bound_addresses = bound
            for index, server in enumerate(servers):
                thread = threading.Thread(
                    target=lambda srv=server: srv.serve_forever(poll_interval=0.25),
                    name=f"UHV-web-{bound[index]}:{candidate_port}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)
            return
        if not self.start_error:
            self.start_error = "No free local browser-monitor port was found."

    def stop(self) -> None:
        servers = list(self._servers)
        self._servers = []
        for server in servers:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        self._threads.clear()
        self.bound_addresses = []
        self.port = None

    def urls(self) -> List[str]:
        if self.port is None:
            return []
        private = [address for address in self.bound_addresses if address != "127.0.0.1"]
        return [f"http://{address}:{self.port}/" for address in private] + [f"http://127.0.0.1:{self.port}/"]

    def local_url(self) -> str:
        if self.port is None:
            return ""
        return f"http://127.0.0.1:{self.port}/"

    def update_panel(self, panel: Any, status: Any) -> None:
        gauges: List[Dict[str, Any]] = []
        panel_status = "Connected" if getattr(status, "ok", False) else "Status error"
        error = getattr(status, "parse_error", "") or ""
        if isinstance(status, NGC3Status):
            order = ["IG1", "IG2", "Pirani 1", "Pirani 2", "Active Gauge"]
            for key in order:
                gauge = status.gauges.get(key)
                if gauge is None:
                    continue
                pressure_mbar = gauge.pressure_mbar
                if pressure_mbar is not None and not math.isfinite(pressure_mbar):
                    pressure_mbar = None
                gauges.append({
                    "key": key,
                    "name": key,
                    "display": gauge.pressure_display,
                    "pressure_mbar": pressure_mbar,
                    "state": "Emission" if gauge.is_emitting else "",
                })
            detail = {
                "remote": bool(status.remote_mode),
                "selected_ig": status.selected_ig,
                "temperature_c": status.temperature_c,
            }
        elif isinstance(status, (PfeifferStatus, ExtendedStatus)):
            keys = getattr(panel, "gauge_keys", PFEIFFER_GAUGE_KEYS)
            for key in keys:
                gauge = status.gauges.get(key)
                if gauge is None:
                    continue
                sensor = f" ({gauge.sensor_id})" if gauge.sensor_id else ""
                pressure_mbar = gauge.pressure_mbar
                if pressure_mbar is not None and not math.isfinite(pressure_mbar):
                    pressure_mbar = None
                gauges.append({
                    "key": key,
                    "name": key + sensor,
                    "display": gauge.pressure_display,
                    "pressure_mbar": pressure_mbar,
                    "state": (panel.gauge_status_label(gauge.status_code) if hasattr(panel, "gauge_status_label")
                              else PFEIFFER_STATUS_LABELS.get(gauge.status_code, f"Status {gauge.status_code}")),
                })
            detail = {"units": status.units, "diagnostics": getattr(status, "diagnostics", {})}
        else:
            detail = {}
        item = {
            "id": panel.port,
            "name": panel.display_name,
            "serial_port": panel.port,
            "device_type": getattr(panel, "device_type", "Pressure controller"),
            "connection": panel_status,
            "updated_at": float(getattr(status, "timestamp", time.time())),
            "updated_text": now_human(float(getattr(status, "timestamp", time.time()))),
            "gauges": gauges,
            "error": error,
            "detail": detail,
        }
        with self.lock:
            self._panels[panel.port] = item

    def update_tdk(self, panel: Any, reading: Any) -> None:
        item = {
            "id": panel.port, "name": panel.display_name, "serial_port": panel.port,
            "device_type": TDK_DEVICE_TYPE, "connection": "Connected",
            "updated_at": reading.timestamp, "updated_text": now_human(reading.timestamp),
            "gauges": [
                {"key": "Voltage", "name": "Measured voltage", "display": f"{reading.voltage_v:.5g} V", "pressure_mbar": None, "state": ""},
                {"key": "Current", "name": "Measured current", "display": f"{reading.current_a:.5g} A", "pressure_mbar": None, "state": ""},
                {"key": "Power", "name": "Measured power", "display": f"{reading.power_w:.5g} W", "pressure_mbar": None, "state": ""},
            ], "error": "", "detail": {"units": "V / A", "diagnostics": {
               "Output": reading.output_state, "Mode": reading.mode, "Model": reading.identity,
               "Programmed V": reading.voltage_set_v, "Programmed A": reading.current_set_a,
            }},
        }
        with self.lock:
            self._panels[panel.port] = item

    def update_error(self, panel: Any, error: str) -> None:
        with self.lock:
            item = dict(self._panels.get(panel.port, {}))
            item.update({
                "id": panel.port,
                "name": panel.display_name,
                "serial_port": panel.port,
                "device_type": getattr(panel, "device_type", "Pressure controller"),
                "connection": "Communication error",
                "error": str(error),
                "updated_at": time.time(),
                "updated_text": now_human(),
            })
            item.setdefault("gauges", [])
            item.setdefault("detail", {})
            self._panels[panel.port] = item

    def remove_panel(self, panel: Any) -> None:
        with self.lock:
            self._panels.pop(panel.port, None)

    def update_alerts(self, alerts: List[AlertEvent]) -> None:
        data = []
        for alert in alerts[:20]:
            data.append({
                "severity": alert.severity,
                "controller": alert.controller_name,
                "message": alert.message,
                "details": alert.details,
                "last_seen": now_human(alert.last_seen),
            })
        with self.lock:
            self._alerts = data

    @staticmethod
    def _range_key_from_label(label: str) -> str:
        return {
            "All": "all",
            "48 h": "48h",
            "24 h": "24h",
            "1 h": "1h",
        }.get(str(label), "24h")

    @staticmethod
    def _downsample_points(points: List[Tuple[float, float]], max_points: int = WEB_GRAPH_MAX_POINTS) -> List[Tuple[float, float]]:
        """Limit browser payload size without changing the stored pressure log."""
        clean = sorted(
            (float(ts), float(value))
            for ts, value in points
            if value is not None and value > 0 and math.isfinite(value)
        )
        if len(clean) <= max_points:
            return clean
        if max_points < 3:
            return [clean[0], clean[-1]]
        last_index = len(clean) - 1
        indexes = {0, last_index}
        for i in range(1, max_points - 1):
            indexes.add(round(i * last_index / (max_points - 1)))
        return [clean[index] for index in sorted(indexes)]

    def theme_snapshot(self) -> Dict[str, Any]:
        graph_colors = list(GRAPH_COLORS) if GRAPH_COLORS else [UNIBAS_RED, UNIBAS_MINT, UNIBAS_ANTHRAZIT_LIGHT]
        return {
            "name": ACTIVE_THEME_FILE or "Built-in University of Basel",
            "mode": "dark" if CURRENT_DARK_MODE else "light",
            "colors": {
                "background": UNIBAS_GRAY6,
                "card": UNIBAS_WHITE,
                "text": UNIBAS_GRAY1,
                "muted": UNIBAS_GRAY2,
                "border": UNIBAS_GRAY5,
                "grid": UNIBAS_GRAY6,
                "mint": UNIBAS_MINT,
                "mint_light": UNIBAS_MINT_LIGHT,
                "red": UNIBAS_RED,
                "header": UNIBAS_ANTHRAZIT,
                "header_active": UNIBAS_ANTHRAZIT_LIGHT,
                "graph_colors": graph_colors,
            },
            "fonts": {
                "heading": str(FONT_H1[0]),
                "body": str(FONT_BODY[0]),
                "button": str(FONT_BUTTON[0]),
                "caption": str(FONT_CAPTION[0]),
            },
        }

    def history_snapshot(self, port: str, range_name: str) -> Dict[str, Any]:
        ranges: Dict[str, Tuple[str, Optional[int]]] = {
            "all": ("All", None),
            "48h": ("48 h", 48 * 3600),
            "24h": ("24 h", 24 * 3600),
            "1h": ("1 h", 3600),
        }
        if range_name not in ranges:
            range_name = "24h"
        range_label, range_seconds = ranges[range_name]

        with self.lock:
            raw_panel = self._panels.get(port)
            if raw_panel is None:
                raise KeyError(port)
            panel = dict(raw_panel)
            panel["gauges"] = [dict(gauge) for gauge in raw_panel.get("gauges", [])]
            panel["detail"] = dict(raw_panel.get("detail", {}))

        if panel.get("device_type") == TDK_DEVICE_TYPE:
            rows = TDK_HISTORY.rows(port, range_seconds)
            max_points = WEB_GRAPH_MAX_POINTS
            if len(rows) > max_points:
                stride = max(1, math.ceil(len(rows)/max_points))
                rows = rows[::stride] + ([rows[-1]] if rows[-1] not in rows[::stride] else [])
            return {
                "port": port, "panel_name": panel.get("name") or port, "range": range_name,
                "range_label": range_label, "units": "V / A", "kind": "electrical",
                "series": [
                    {"key": "Voltage", "name": "Voltage [V]", "points": [[r[0],r[1]] for r in rows]},
                    {"key": "Current", "name": "Current [A]", "points": [[r[0],r[2]] for r in rows]},
                ], "generated_at": time.time(),
            }
        settings = alert_manager.settings
        if panel.get("device_type") == NGC3_DEVICE_TYPE:
            if settings.graph_selected_ig_only:
                visible = [str(panel.get("detail", {}).get("selected_ig") or "IG1")]
            else:
                visible = [key for key in settings.graph_visible_gauges if key in GRAPH_GAUGE_KEYS]
                if not visible:
                    visible = list(GRAPH_GAUGE_KEYS)
        elif panel.get("device_type") == PFEIFFER_DEVICE_TYPE:
            visible = list(PFEIFFER_GAUGE_KEYS)
        else:
            # Each additional controller has its own channel keys; using the
            # Pfeiffer-only list here hid Agilent/EpiMax browser graphs.
            visible = [str(gauge.get("key")) for gauge in panel.get("gauges", []) if gauge.get("key")]

        stored = pressure_logger.query_series(port, range_seconds)
        updated_at = float(panel.get("updated_at") or time.time())
        live_by_key = {
            str(gauge.get("key") or gauge.get("name")): gauge
            for gauge in panel.get("gauges", [])
        }
        series = []
        for key in visible:
            values = list(stored.get(key, []))
            live = live_by_key.get(key, {})
            live_pressure = safe_float(live.get("pressure_mbar"))
            if live_pressure is not None and live_pressure > 0 and math.isfinite(live_pressure):
                if not values or abs(float(values[-1][0]) - updated_at) > 0.01:
                    values.append((updated_at, float(live_pressure)))
            values = self._downsample_points(values)
            if not values:
                continue
            series.append({
                "key": key,
                "name": str(live.get("name") or key),
                "points": [[ts, pressure] for ts, pressure in values],
            })

        return {
            "port": port,
            "panel_name": panel.get("name") or port,
            "range": range_name,
            "range_label": range_label,
            "units": "mBar",
            "series": series,
            "generated_at": time.time(),
        }

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            panels = []
            for raw_item in self._panels.values():
                item = dict(raw_item)
                item["gauges"] = [dict(gauge) for gauge in raw_item.get("gauges", [])]
                item["detail"] = dict(raw_item.get("detail", {}))
                panels.append(item)
            alerts = [dict(item) for item in self._alerts]
        panels.sort(key=lambda item: (str(item.get("name", "")).lower(), str(item.get("serial_port", ""))))
        return {
            "title": APP_TITLE,
            "hostname": platform.node(),
            "server_time": now_human(),
            "read_only": True,
            "theme": self.theme_snapshot(),
            "graph": {
                "default_range": self._range_key_from_label(alert_manager.settings.graph_default_range),
                "ranges": [
                    {"key": "all", "label": "All"},
                    {"key": "48h", "label": "48 h"},
                    {"key": "24h", "label": "24 h"},
                    {"key": "1h", "label": "1 h"},
                ],
            },
            "panels": panels,
            "alerts": alerts,
        }

    @staticmethod
    def _dashboard_html() -> str:
        return r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>UHV Sentinel — Pressure Dashboard</title>
<style>
:root{color-scheme:light;--bg:#eaebec;--card:#fff;--text:#333;--muted:#666;--line:#d9d9d9;--grid:#eaebec;--mint:#a5d7d2;--mint-light:#d2ebe9;--red:#d20537;--header:#2d373c;--header-active:#46505a;--heading-font:"PT Serif",Georgia,serif;--body-font:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;--button-font:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
*{box-sizing:border-box}html,body{min-height:100%}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.42 var(--body-font)}
header{background:var(--header);color:#fff;border-bottom:7px solid var(--mint);padding:18px 22px}.header-inner{max-width:1500px;margin:0 auto;display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap}
header h1{margin:0;font:700 25px/1.18 var(--heading-font)}header p{margin:5px 0 0;color:var(--mint-light);font-size:13px}.read-only{align-self:center;border:1px solid var(--mint);background:var(--header-active);padding:6px 10px;font:600 12px var(--button-font);letter-spacing:.02em}
main{max-width:1500px;margin:0 auto;padding:17px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:13px}.meta{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:13px}.range-control{display:flex;align-items:center;gap:8px}.range-control label{color:var(--muted);font-size:13px}
select{appearance:auto;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:2px;padding:7px 28px 7px 9px;font:600 13px var(--button-font)}.notice{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--mint);padding:9px 12px;margin-bottom:13px}.alert{border-left-color:var(--red)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.card{min-width:0;background:var(--card);border:1px solid var(--line);padding:15px;box-shadow:0 1px 2px #0001}.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.card h2{font:700 20px/1.25 var(--heading-font);margin:0}.connection{font-size:12px;padding:4px 7px;background:var(--mint-light);color:var(--header);white-space:nowrap}.connection.error-state{background:var(--red);color:#fff}
.sub{color:var(--muted);font-size:12px;margin:4px 0 10px}.details{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:8px}.chip{border:1px solid var(--line);padding:3px 6px;color:var(--muted);font-size:11px}.gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:8px;margin:8px 0 13px}.gauge{border:1px solid var(--line);padding:9px 10px;min-width:0}.gauge-name{font-weight:600;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pressure{font:700 19px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace;margin-top:3px;white-space:nowrap}.state{color:var(--muted);font-size:11px;margin-top:2px;min-height:1.2em}
.error{color:var(--red);white-space:pre-wrap;margin:7px 0}.chart-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;border-top:1px solid var(--line);padding-top:10px}.chart-title{font:700 14px var(--heading-font)}.chart-range{font-size:11px;color:var(--muted)}.chart-wrap{position:relative;width:100%;height:280px;margin-top:5px;background:var(--card)}canvas{display:block;width:100%;height:280px}.chart-empty{position:absolute;inset:0;display:none;align-items:center;justify-content:center;color:var(--muted);font-size:13px}.tooltip{position:absolute;display:none;pointer-events:none;z-index:4;background:var(--header);color:#fff;border:1px solid var(--mint);padding:6px 8px;font-size:11px;white-space:nowrap;box-shadow:0 2px 6px #0003}
.empty{color:var(--muted);padding:28px;text-align:center;background:var(--card);border:1px solid var(--line)}footer{max-width:1500px;margin:0 auto;padding:0 17px 22px;color:var(--muted);font-size:12px}@media(max-width:900px){.grid{grid-template-columns:1fr}.chart-wrap,canvas{height:250px}}@media(max-width:520px){main{padding:10px}.gauges{grid-template-columns:1fr}.chart-wrap,canvas{height:230px}}
</style>
</head>
<body>
<header><div class="header-inner"><div><h1>UHV Sentinel</h1><p>Local pressure and power dashboard</p></div><div class="read-only">READ ONLY</div></div></header>
<main>
<div class="topbar"><div class="meta"><span id="host">Host: —</span><span id="time">Updated: —</span><span id="themeName">Theme: —</span></div><div class="range-control"><label for="range">Graph range</label><select id="range"><option value="all">All</option><option value="48h">48 h</option><option value="24h" selected>24 h</option><option value="1h">1 h</option></select></div></div>
<div class="notice">Read-only monitoring. Ion-gauge and power-supply controls are unavailable in the browser.</div>
<div id="alerts"></div><div id="panels" class="grid"><div class="empty">Waiting for pressure data…</div></div>
</main><footer>Accessible only through addresses on the monitor computer and its private local network. No router port forwarding or public internet service is configured.</footer>
<script>
'use strict';
const panelsRoot=document.getElementById('panels'),alertsRoot=document.getElementById('alerts'),rangeSelect=document.getElementById('range');
const nodes=new Map();let currentTheme=null;let rangeInitialized=false;let graphUpdateBusy=false;
function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node}
function css(name,value){if(value)document.documentElement.style.setProperty(name,value)}
function applyTheme(theme){if(!theme)return;currentTheme=theme;const c=theme.colors||{},f=theme.fonts||{};document.documentElement.style.colorScheme=theme.mode==='dark'?'dark':'light';css('--bg',c.background);css('--card',c.card);css('--text',c.text);css('--muted',c.muted);css('--line',c.border);css('--grid',c.grid);css('--mint',c.mint);css('--mint-light',c.mint_light);css('--red',c.red);css('--header',c.header);css('--header-active',c.header_active);if(f.heading)css('--heading-font','"'+f.heading.replaceAll('"','')+'",Georgia,serif');if(f.body)css('--body-font','"'+f.body.replaceAll('"','')+'",system-ui,-apple-system,"Segoe UI",sans-serif');if(f.button)css('--button-font','"'+f.button.replaceAll('"','')+'",system-ui,-apple-system,"Segoe UI",sans-serif');document.getElementById('themeName').textContent='Theme: '+(theme.name||'Built-in')+' · '+(theme.mode||'light')}
function detailChips(p){const values=[];const d=p.detail||{};if(typeof d.remote==='boolean')values.push('Remote: '+(d.remote?'yes':'no'));if(d.selected_ig)values.push('Selected: '+d.selected_ig);if(d.temperature_c!==null&&d.temperature_c!==undefined)values.push('Temp: '+d.temperature_c+' °C');if(d.units)values.push('Units: '+d.units);return values}
function makePanel(id){const card=el('section','card');const head=el('div','card-head');const title=el('h2','');const connection=el('div','connection');head.append(title,connection);const sub=el('div','sub');const details=el('div','details');const error=el('div','error');const gauges=el('div','gauges');const diagnostics=el('details','diagnostics');const diagSummary=el('summary','', 'Controller diagnostics');const diagBody=el('pre','');diagBody.style.whiteSpace='pre-wrap';diagBody.style.overflowWrap='anywhere';diagBody.style.fontSize='12px';diagnostics.append(diagSummary,diagBody);const chartHead=el('div','chart-head');const chartTitle=el('div','chart-title','Pressure history [mBar]');const chartRange=el('div','chart-range','');chartHead.append(chartTitle,chartRange);const wrap=el('div','chart-wrap');const canvas=document.createElement('canvas');canvas.setAttribute('aria-label','Pressure history graph');const empty=el('div','chart-empty','No pressure data');const tooltip=el('div','tooltip');wrap.append(canvas,empty,tooltip);card.append(head,sub,details,error,gauges,diagnostics,chartHead,wrap);panelsRoot.append(card);const item={id,card,title,connection,sub,details,error,gauges,diagnostics,diagBody,canvas,empty,tooltip,chartRange,chartTitle,history:null};canvas.addEventListener('mousemove',event=>showTooltip(item,event));canvas.addEventListener('mouseleave',()=>{tooltip.style.display='none'});nodes.set(id,item);return item}
function renderStatus(data){applyTheme(data.theme);document.getElementById('host').textContent='Host: '+(data.hostname||'—');document.getElementById('time').textContent='Updated: '+(data.server_time||'—');if(!rangeInitialized&&data.graph&&data.graph.default_range){rangeSelect.value=data.graph.default_range;rangeInitialized=true}alertsRoot.replaceChildren();if(data.alerts&&data.alerts.length){const box=el('div','notice alert');const first=data.alerts[0];box.append(el('strong','',data.alerts.length+' active alert(s): '));box.append(document.createTextNode(String(first.severity||'').toUpperCase()+' '+(first.controller||'')+': '+(first.message||'')));alertsRoot.append(box)}const active=new Set(),list=data.panels||[];if(!list.length){panelsRoot.replaceChildren(el('div','empty','Waiting for pressure data…'));nodes.clear();return}if(panelsRoot.querySelector(':scope > .empty'))panelsRoot.replaceChildren();for(const p of list){const id=String(p.id||p.serial_port||'');active.add(id);const n=nodes.get(id)||makePanel(id);n.title.textContent=p.name||p.serial_port||'Pressure controller';n.connection.textContent=p.connection||'Unknown';n.connection.classList.toggle('error-state',Boolean(p.error)||String(p.connection||'').toLowerCase().includes('error'));n.sub.textContent=(p.device_type||'')+' · '+(p.serial_port||'')+' · '+(p.updated_text||'—');n.details.replaceChildren(...detailChips(p).map(value=>el('span','chip',value)));const diags=(p.detail||{}).diagnostics||{};const diagRows=Object.entries(diags);n.diagnostics.style.display=diagRows.length?'block':'none';n.diagBody.textContent=diagRows.map(([key,val])=>key+': '+val).join('\n');n.error.textContent=p.error||'';n.error.style.display=p.error?'block':'none';n.gauges.replaceChildren();for(const g of (p.gauges||[])){const box=el('div','gauge');box.append(el('div','gauge-name',g.name||g.key||'Gauge'),el('div','pressure',g.display||'---'),el('div','state',g.state||''));n.gauges.append(box)}if(!(p.gauges||[]).length)n.gauges.append(el('div','sub','No valid gauge values received.'))}for(const [id,n] of [...nodes])if(!active.has(id)){n.card.remove();nodes.delete(id)}}
function scientific(value){if(!Number.isFinite(value)||value<=0)return '—';return value.toExponential(1).replace('e+','e')}
function graphColors(){const c=(currentTheme&&currentTheme.colors&&currentTheme.colors.graph_colors)||[];return c.length?c:['#d20537','#a5d7d2','#46505a','#666666','#8a6f00']}
function drawElectricalGraph(n,history){
 n.chartTitle.textContent='Actual voltage [V] / current [A]';
 const series=history.series||[],vs=(series[0]||{}).points||[],cs=(series[1]||{}).points||[];
 const pts=[...vs,...cs];if(!pts.length){n.empty.textContent='Awaiting electrical measurements';n.empty.style.display='flex';return}
 n.empty.style.display='none';const rect=n.canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1),w=Math.max(320,rect.width),h=Math.max(220,rect.height);
 n.canvas.width=Math.round(w*dpr);n.canvas.height=Math.round(h*dpr);const ctx=n.canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
 const left=64,right=w-64,top=24,bottom=h-42;
 let minT=Math.min(...pts.map(p=>Number(p[0]))),maxT=Math.max(...pts.map(p=>Number(p[0])));if(minT===maxT)maxT=minT+60;
 const maxV=Math.max(.1,...vs.map(p=>Number(p[1])))*1.1,maxI=Math.max(.01,...cs.map(p=>Number(p[1])))*1.1;
 const x=ts=>left+(Number(ts)-minT)/(maxT-minT)*(right-left),y=(v,m)=>bottom-Math.max(0,Number(v))/m*(bottom-top);
 ctx.strokeStyle='#cccccc';ctx.strokeRect(left,top,right-left,bottom-top);ctx.font='11px system-ui';
 for(let j=0;j<=4;j++){const f=j/4,yy=bottom-f*(bottom-top);ctx.strokeStyle='#dddddd';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(right,yy);ctx.stroke();ctx.fillStyle='#d20537';ctx.textAlign='right';ctx.fillText((maxV*f).toPrecision(2),left-7,yy);ctx.fillStyle='#1864ab';ctx.textAlign='left';ctx.fillText((maxI*f).toPrecision(2),right+7,yy)}
 n._hover=[];for(const [arr,m,color,name] of [[vs,maxV,'#d20537','Voltage [V]'],[cs,maxI,'#1864ab','Current [A]']]){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();arr.forEach((p,i)=>{const px=x(p[0]),py=y(p[1],m);if(i===0)ctx.moveTo(px,py);else ctx.lineTo(px,py);n._hover.push({x:px,y:py,name,ts:Number(p[0]),pressure:Number(p[1])})});ctx.stroke()}
 ctx.textAlign='left';ctx.fillStyle='#d20537';ctx.fillText('Voltage [V]',left,14);ctx.textAlign='right';ctx.fillStyle='#1864ab';ctx.fillText('Current [A]',right,14);
 ctx.fillStyle='#777';ctx.textAlign='left';ctx.fillText(new Date(minT*1000).toLocaleString(),left,bottom+20);ctx.textAlign='right';ctx.fillText(new Date(maxT*1000).toLocaleString(),right,bottom+20);
 n.chartRange.textContent=history.range_label||'';
}
function drawGraph(n,history){n.history=history;
if(history.kind==='electrical'){drawElectricalGraph(n,history);return;}
n.chartTitle.textContent='Pressure history [mBar]';const all=[];for(const s of (history.series||[]))for(const p of (s.points||[]))if(Number(p[0])&&Number(p[1])>0)all.push([Number(p[0]),Number(p[1])]);if(!all.length){n.empty.style.display='flex';const ctx=n.canvas.getContext('2d');ctx.clearRect(0,0,n.canvas.width,n.canvas.height);return}n.empty.style.display='none';const rect=n.canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1),w=Math.max(320,rect.width),h=Math.max(220,rect.height);n.canvas.width=Math.round(w*dpr);n.canvas.height=Math.round(h*dpr);const ctx=n.canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);const styles=getComputedStyle(document.documentElement),card=styles.getPropertyValue('--card').trim(),text=styles.getPropertyValue('--text').trim(),muted=styles.getPropertyValue('--muted').trim(),line=styles.getPropertyValue('--line').trim(),grid=styles.getPropertyValue('--grid').trim();ctx.fillStyle=card;ctx.fillRect(0,0,w,h);const left=82,right=w-17,top=24,bottom=h-48;ctx.strokeStyle=line;ctx.strokeRect(left,top,right-left,bottom-top);let minT=Math.min(...all.map(p=>p[0])),maxT=Math.max(...all.map(p=>p[0])),minP=Math.min(...all.map(p=>p[1])),maxP=Math.max(...all.map(p=>p[1]));if(minT===maxT){minT-=60;maxT+=60}let logMin=Math.log10(minP),logMax=Math.log10(maxP);if(logMin===logMax){logMin-=.18;logMax+=.18}else{const span=logMax-logMin,pad=Math.max(.05,Math.min(.35,span*.12));logMin-=pad;logMax+=pad;if(logMax-logMin<.30){const centre=(logMin+logMax)/2;logMin=centre-.15;logMax=centre+.15}}const x=ts=>left+(ts-minT)/(maxT-minT)*(right-left),y=p=>bottom-(Math.log10(p)-logMin)/(logMax-logMin)*(bottom-top);ctx.font='11px '+styles.getPropertyValue('--body-font');ctx.textBaseline='middle';let lastLabel=-999;for(let exp=Math.floor(logMin)-1;exp<=Math.ceil(logMax)+1;exp++){for(const coeff of [1,2,5]){const value=coeff*Math.pow(10,exp),lv=Math.log10(value);if(lv<logMin||lv>logMax)continue;const py=y(value);ctx.strokeStyle=grid;ctx.setLineDash(coeff===1?[]:[2,4]);ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(right,py);ctx.stroke();if(Math.abs(py-lastLabel)>=15){ctx.fillStyle=muted;ctx.textAlign='right';ctx.fillText(value.toExponential(0).replace('e+','e'),left-8,py);lastLabel=py}}}ctx.setLineDash([]);const ticks=w<620?5:7,durationH=(maxT-minT)/3600,spansDates=new Date(minT*1000).toDateString()!==new Date(maxT*1000).toDateString();ctx.textAlign='center';ctx.textBaseline='top';for(let i=0;i<ticks;i++){const frac=i/(ticks-1),ts=minT+frac*(maxT-minT),px=x(ts),hour=frac*durationH;ctx.strokeStyle=grid;ctx.beginPath();ctx.moveTo(px,top);ctx.lineTo(px,bottom);ctx.stroke();ctx.fillStyle=muted;const hourText=durationH<=2?hour.toFixed(1)+' h':Math.round(hour)+' h';const date=new Date(ts*1000);const dateText=String(date.getDate()).padStart(2,'0')+'.'+String(date.getMonth()+1).padStart(2,'0');ctx.fillText(spansDates?hourText+' · '+dateText:hourText,px,bottom+8)}ctx.save();ctx.translate(15,(top+bottom)/2);ctx.rotate(-Math.PI/2);ctx.fillStyle=muted;ctx.textAlign='center';ctx.textBaseline='top';ctx.fillText('Pressure [mBar]',0,0);ctx.restore();ctx.fillStyle=muted;ctx.textAlign='center';ctx.textBaseline='bottom';ctx.fillText('Time [hours]',(left+right)/2,h-3);const colors=graphColors();n._hover=[];let legendY=top+9;for(let index=0;index<(history.series||[]).length;index++){const s=history.series[index],pts=(s.points||[]).filter(p=>Number(p[1])>0),color=colors[index%colors.length];if(!pts.length)continue;ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();pts.forEach((p,i)=>{const px=x(Number(p[0])),py=y(Number(p[1]));if(i===0)ctx.moveTo(px,py);else ctx.lineTo(px,py);n._hover.push({x:px,y:py,name:s.name||s.key,ts:Number(p[0]),pressure:Number(p[1])})});ctx.stroke();ctx.fillStyle=color;for(const p of pts){const px=x(Number(p[0])),py=y(Number(p[1]));ctx.beginPath();ctx.arc(px,py,2.1,0,Math.PI*2);ctx.fill()}ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(left+10,legendY);ctx.lineTo(left+28,legendY);ctx.stroke();ctx.fillStyle=text;ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(s.name||s.key,left+34,legendY);legendY+=16}n.chartRange.textContent=history.range_label||''}
function showTooltip(n,event){const points=n._hover||[];if(!points.length){n.tooltip.style.display='none';return}const rect=n.canvas.getBoundingClientRect(),mx=event.clientX-rect.left,my=event.clientY-rect.top;let best=null,bestD=Infinity;for(const p of points){const d=(p.x-mx)**2+(p.y-my)**2;if(d<bestD){best=p;bestD=d}}if(!best||bestD>18**2){n.tooltip.style.display='none';return}const date=new Date(best.ts*1000);n.tooltip.textContent=best.name+' · '+scientific(best.pressure)+' mBar · '+date.toLocaleString();n.tooltip.style.display='block';const px=Math.min(rect.width-n.tooltip.offsetWidth-6,Math.max(6,best.x+10)),py=Math.min(rect.height-n.tooltip.offsetHeight-6,Math.max(6,best.y-30));n.tooltip.style.left=px+'px';n.tooltip.style.top=py+'px'}
async function updateStatus(){try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);renderStatus(await response.json())}catch(error){panelsRoot.replaceChildren(el('div','empty','Local monitor unavailable: '+error.message));nodes.clear()}}
async function updateGraphs(){if(graphUpdateBusy)return;graphUpdateBusy=true;try{await Promise.all([...nodes.values()].map(async n=>{try{const url='/api/history?port='+encodeURIComponent(n.id)+'&range='+encodeURIComponent(rangeSelect.value);const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);drawGraph(n,await response.json())}catch(error){n.empty.textContent='Pressure graph unavailable: '+error.message;n.empty.style.display='flex'}}))}finally{graphUpdateBusy=false}}
rangeSelect.addEventListener('change',updateGraphs);window.addEventListener('resize',()=>{for(const n of nodes.values())if(n.history)drawGraph(n,n.history)});updateStatus().then(updateGraphs);setInterval(updateStatus,2000);setInterval(updateGraphs,5000);
</script>
</body></html>'''

class ScrollableFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, background=UNIBAS_GRAY6)
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.inner = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vscroll.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)
        self.canvas.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        # Keep the inner frame exactly as wide as the visible canvas.
        # The panel grid then always divides this width into two columns.
        self.canvas.itemconfigure(self.window_id, width=event.width)
        top = self.winfo_toplevel()
        if hasattr(top, "schedule_relayout"):
            top.schedule_relayout()

    def _bind_mousewheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _wheel_units(self, event) -> int:
        if getattr(event, "delta", 0) == 0:
            return 0
        return -1 if event.delta > 0 else 1

    def _on_mousewheel(self, event):
        units = self._wheel_units(event)
        if units:
            self.canvas.yview_scroll(units, "units")



class DevicePanel(ttk.LabelFrame):
    device_type = NGC3_DEVICE_TYPE

    def __init__(self, parent, app: "UHVSentinelApp", name: str, port: str, baudrate: int = DEFAULT_BAUD,
                 saved_state: Optional[Dict[str, Any]] = None):
        super().__init__(parent, text=name, style="Card.TLabelframe")
        self.app = app
        self.name_var = tk.StringVar(value=name)
        self.port = port
        self.device = NGC3Device(port=port, baudrate=baudrate)
        saved_state = saved_state or {}
        saved_emission = saved_state.get("last_emission_request", "off")
        if saved_emission in ("0.5mA", "5mA"):
            self.device.last_emission_request = saved_emission
            self.device.five_ma_unlocked = bool(saved_state.get("five_ma_unlocked", saved_emission in ("0.5mA", "5mA")))
        self.device.software_auto = bool(saved_state.get("software_auto", False))
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.selected_ig_var = tk.IntVar(value=int(saved_state.get("selected_ig", 1) or 1))
        graph_range = saved_state.get("graph_range", alert_manager.settings.graph_default_range)
        if graph_range not in GRAPH_RANGE_OPTIONS:
            graph_range = alert_manager.settings.graph_default_range if alert_manager.settings.graph_default_range in GRAPH_RANGE_OPTIONS else "24 h"
        self.graph_range_var = tk.StringVar(value=graph_range)
        self._graph_redraw_after: Optional[str] = None
        self._latest_status: Optional[NGC3Status] = None
        self._last_graph_draw_at = 0.0
        self.last_logged_at = 0.0
        self.connection_var = tk.StringVar(value="Disconnected")
        self.remote_var = tk.StringVar(value="Remote: ?")
        self.selected_var = tk.StringVar(value="Selected IG: ?")
        self.last_update_var = tk.StringVar(value="Last update: ---")
        self.error_var = tk.StringVar(value="")
        self.pressure_vars = {key: tk.StringVar(value="---") for key in ["IG1", "IG2", "Pirani 1", "Pirani 2", "Active Gauge"]}
        self.status_text_var = tk.StringVar(value="Instrument flags: ---")
        self.error_text_var = tk.StringVar(value="Error flags: ---")
        self.relay_var = tk.StringVar(value="Relays: ---")
        self.temp_var = tk.StringVar(value="Temp: ---")
        self.gauge_flags_var = tk.StringVar(value="Gauge flags: ---")
        self.gauge_error_flags_var = tk.StringVar(value="Gauge errors: ---")
        self.raw_var = tk.StringVar(value="Raw: ---")
        self.auto_hint_var = tk.StringVar(value="")
        self.alert_summary_var = tk.StringVar(value="Alerts: none")
        self.log_status_var = tk.StringVar(value="Last log: —")
        self.pressure_history: Dict[str, List[Tuple[float, float]]] = {}
        self.recommendation: Optional[str] = None  # increase_5mA or decrease_05mA
        self._blink_state = False
        self._blink_after: Optional[str] = None
        self._last_auto_action: Dict[str, float] = {}
        self._last_error_at = 0.0

        self.btn_05: Optional[ColorButton] = None
        self.btn_5: Optional[ColorButton] = None
        self.btn_auto: Optional[ColorButton] = None
        self.btn_off: Optional[ColorButton] = None
        self.controls_frame: Optional[ttk.LabelFrame] = None
        self.graph_canvas: Optional[tk.Canvas] = None
        self._graph_hover_points: List[Tuple[float, float, str, float, float, str]] = []
        self._build()
        self._connect()

    @property
    def display_name(self) -> str:
        return self.name_var.get().strip() or self.port

    def state_for_config(self) -> Dict[str, Any]:
        return {
            "last_emission_request": self.device.last_emission_request,
            "five_ma_unlocked": self.device.five_ma_unlocked,
            "software_auto": self.device.software_auto,
            "selected_ig": self.selected_ig_var.get(),
            "graph_range": self.graph_range_var.get(),
        }

    def _build(self):
        try:
            self.configure(width=PANEL_MIN_WIDTH)
        except Exception:
            pass
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 4))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text=f"Port: {self.port}").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.connection_var).grid(row=0, column=1, sticky="e", padx=(8, 8))
        ttk.Button(top, text="Remove panel", command=self.remove).grid(row=0, column=2, sticky="e")

        alert_box = ttk.LabelFrame(self, text="Alerts")
        alert_box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        alert_box.columnconfigure(0, weight=1)
        ttk.Label(alert_box, textvariable=self.alert_summary_var, wraplength=820, justify="left").grid(row=0, column=0, sticky="ew", padx=4, pady=2)

        pressures = ttk.LabelFrame(self, text="Pressures")
        pressures.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        pressures.columnconfigure(1, weight=1)
        for row, key in enumerate(["IG1", "IG2", "Pirani 1", "Pirani 2", "Active Gauge"]):
            ttk.Label(pressures, text=f"{key}:").grid(row=row, column=0, sticky="w", padx=4, pady=1)
            ttk.Label(pressures, textvariable=self.pressure_vars[key], font=FONT_H6).grid(
                row=row, column=1, sticky="e", padx=4, pady=1
            )

        graph = ttk.LabelFrame(self, text="Pressure graph")
        graph.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        graph.columnconfigure(2, weight=1)
        ttk.Label(graph, text="Range:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        range_combo = ttk.Combobox(graph, textvariable=self.graph_range_var, values=GRAPH_RANGE_OPTIONS, width=8, state="readonly")
        range_combo.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        range_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_graph_range_changed())
        ttk.Label(graph, textvariable=self.log_status_var).grid(row=0, column=2, sticky="e", padx=4, pady=2)
        self.graph_canvas = tk.Canvas(graph, width=GRAPH_CANVAS_WIDTH, height=GRAPH_CANVAS_HEIGHT, background=UNIBAS_WHITE, highlightthickness=1, highlightbackground=UNIBAS_GRAY5)
        self.graph_canvas.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 4))
        self.graph_canvas.bind("<Configure>", lambda _event: self.schedule_graph_redraw())
        self.graph_canvas.bind("<Motion>", self._on_graph_motion)
        self.graph_canvas.bind("<Leave>", self._clear_graph_hover)

        controls = ttk.LabelFrame(self, text="Ion gauge control")
        self.controls_frame = controls
        controls.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        for c in range(4):
            controls.columnconfigure(c, weight=1)
        ttk.Label(controls, text="Gauge:").grid(row=0, column=0, padx=4, pady=3, sticky="w")
        ttk.Radiobutton(controls, text="IG1", variable=self.selected_ig_var, value=1, command=self.on_selected_ig_changed).grid(row=0, column=1, padx=4, sticky="w")
        ttk.Radiobutton(controls, text="IG2", variable=self.selected_ig_var, value=2, command=self.on_selected_ig_changed).grid(row=0, column=2, padx=4, sticky="w")

        self.btn_05 = ColorButton(controls, text="0.5 mA", command=self.on_start_05)
        self.btn_05.grid(row=1, column=0, padx=4, pady=4, sticky="ew")
        self.btn_5 = ColorButton(controls, text="5 mA", command=self.on_start_5)
        self.btn_5.grid(row=1, column=1, padx=4, pady=4, sticky="ew")
        self.btn_auto = ColorButton(controls, text="AUTO", command=self.on_toggle_auto)
        self.btn_auto.grid(row=1, column=2, padx=4, pady=4, sticky="ew")
        self.btn_off = ColorButton(controls, text="OFF", command=self.on_off)
        self.btn_off.grid(row=1, column=3, padx=4, pady=4, sticky="ew")
        ttk.Label(controls, textvariable=self.auto_hint_var, wraplength=820, justify="left").grid(
            row=2, column=0, columnspan=4, padx=4, pady=(0, 3), sticky="ew"
        )
        self.update_button_states()
        self.refresh_remote_control_visibility()

        misc = ttk.Frame(controls)
        misc.grid(row=3, column=0, columnspan=4, sticky="ew")
        ttk.Button(misc, text="Reset errors", command=self.on_reset_errors).pack(side="left", padx=4, pady=3)
        ttk.Button(misc, text="Release local", command=self.on_release_local).pack(side="left", padx=4, pady=3)

        info = ttk.LabelFrame(self, text="Status and flags")
        info.grid(row=5, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 8))
        info.columnconfigure(0, weight=1)
        for row, var in enumerate([
            self.remote_var,
            self.selected_var,
            self.relay_var,
            self.temp_var,
            self.status_text_var,
            self.error_text_var,
            self.gauge_flags_var,
            self.gauge_error_flags_var,
            self.last_update_var,
            self.raw_var,
            self.error_var,
        ]):
            ttk.Label(info, textvariable=var, wraplength=820, justify="left").grid(
                row=row, column=0, sticky="ew", padx=4, pady=1
            )

    def refresh_remote_control_visibility(self):
        controls = self.controls_frame
        if controls is None:
            return
        if alert_manager.settings.remote_control_enabled:
            controls.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        else:
            controls.grid_remove()
        try:
            self.app.schedule_relayout()
        except Exception:
            pass

    def _connect(self):
        try:
            self.device.open()
            self.connection_var.set("Connected")
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._poll_loop, name=f"NGC3-{self.port}", daemon=True)
            self.thread.start()
        except Exception as exc:
            self.connection_var.set("Connection failed")
            self.set_error(str(exc))
            self.update_button_states()
            self._force_off_button_red()
            self.after(50, self._force_off_button_red)
            self.after(250, self._force_off_button_red)
            self.after(100, self.draw_pressure_graph)

    def _poll_loop(self):
        while not self.stop_event.is_set():
            try:
                status = self.device.read_status()
                self.app.events.put(("status", self, status))
            except Exception as exc:
                self.app.events.put(("error", self, str(exc)))
            self.stop_event.wait(POLL_INTERVAL_S)

    def update_status(self, status: NGC3Status):
        self._latest_status = status
        self.app.web_monitor.update_panel(self, status)
        if not status.ok:
            self.error_var.set(f"Status parse error: {status.parse_error}")
            self.raw_var.set(f"Raw: {status.raw.hex(' ')[:160]}")
            self.evaluate_and_publish_alerts(status)
            return

        self.error_var.set("")
        for key, var in self.pressure_vars.items():
            g = status.gauges.get(key)
            var.set(g.pressure_display if g else "---")

        self.remote_var.set("Remote: yes" if status.remote_mode else "Remote: no / local")
        self.selected_var.set(f"Selected IG: {status.selected_ig}")
        relays = ", ".join(f"{k}={'ON' if v else 'off'}" for k, v in status.relay_states.items())
        self.relay_var.set(f"Relays: {relays}")
        self.temp_var.set(f"Temp: {status.temperature_c} °C" if status.temperature_c is not None else "Temp: ---")
        self.status_text_var.set(
            f"Instrument flags: type={status.state_byte & 0x0F}, "
            f"state_byte=0x{status.state_byte:02X} ({format_bits(status.state_byte, STATE_BYTE_FLAGS)})"
        )
        self.error_text_var.set(
            f"Error flags: error_byte=0x{status.error_byte:02X} ({format_bits(status.error_byte, ERROR_BYTE_FLAGS)})"
        )

        gauge_flag_lines = []
        gauge_error_lines = []
        for key in ["IG1", "IG2", "Pirani 1", "Pirani 2", "Active Gauge"]:
            g = status.gauges.get(key)
            if not g:
                continue
            if g.gauge_type == "I":
                gauge_flag_lines.append(f"{key}: 0x{g.status_byte:02X} ({format_bits(g.status_byte, ION_STATUS_FLAGS)})")
                gauge_error_lines.append(f"{key}: 0x{g.error_byte:02X} ({format_bits(g.error_byte, ION_ERROR_FLAGS)})")
            elif g.gauge_type == "P":
                gauge_flag_lines.append(f"{key}: 0x{g.status_byte:02X} ({format_bits(g.status_byte, PIRANI_STATUS_FLAGS)})")
                gauge_error_lines.append(f"{key}: 0x{g.error_byte:02X} ({format_bits(g.error_byte, PIRANI_ERROR_FLAGS)})")
            else:
                gauge_flag_lines.append(f"{key}: status=0x{g.status_byte:02X}")
                gauge_error_lines.append(f"{key}: error=0x{g.error_byte:02X}")
        self.gauge_flags_var.set("Gauge flags: " + " | ".join(gauge_flag_lines))
        self.gauge_error_flags_var.set("Gauge errors: " + " | ".join(gauge_error_lines))
        self.last_update_var.set("Last update: " + time.strftime("%H:%M:%S"))
        self.raw_var.set(f"Raw: {status.raw.hex(' ')[:160]}")

        # Restore a visible emission-current state after restart.  The status report
        # tells us whether an ion gauge is in emission but not the real emission current.
        selected_gauge = status.selected_ig
        selected_record = status.gauges.get(selected_gauge)
        if selected_record and selected_record.is_emitting:
            self.selected_ig_var.set(2 if selected_gauge == "IG2" else 1)
            self.device.set_runtime_emission_state(self.device.last_emission_request, True)
        elif not status.any_ig_emitting:
            self.device.set_runtime_emission_state("off", False)

        self.update_pressure_history(status)
        pressure_logger.maybe_log_status(self, status)
        self.request_graph_redraw(status)
        self.update_auto_recommendation(status)
        self.evaluate_and_publish_alerts(status)
        self.update_button_states()
        self.app.request_save_config()

    def request_graph_redraw(self, status: Optional[NGC3Status] = None, *, force: bool = False) -> None:
        if status is not None:
            self._latest_status = status
        elapsed = time.monotonic() - self._last_graph_draw_at
        if force or elapsed >= GRAPH_REFRESH_INTERVAL_S:
            if self._graph_redraw_after is not None:
                try:
                    self.after_cancel(self._graph_redraw_after)
                except Exception:
                    pass
                self._graph_redraw_after = None
            self.draw_pressure_graph(self._latest_status)
            return
        if self._graph_redraw_after is None:
            delay_ms = max(120, int((GRAPH_REFRESH_INTERVAL_S - elapsed) * 1000))
            self._graph_redraw_after = self.after(delay_ms, self._run_scheduled_graph_redraw)

    def schedule_graph_redraw(self):
        self.request_graph_redraw(force=False)

    def _run_scheduled_graph_redraw(self):
        self._graph_redraw_after = None
        self.draw_pressure_graph(self._latest_status)


    def on_selected_ig_changed(self):
        self.app.save_config()
        self.request_graph_redraw(force=True)

    def on_graph_range_changed(self):
        self.app.save_config()
        self.request_graph_redraw(force=True)

    def _graph_range_seconds(self) -> Optional[int]:
        return GRAPH_RANGE_SECONDS.get(self.graph_range_var.get(), 24 * 3600)

    def _visible_graph_gauges(self) -> List[str]:
        settings = alert_manager.settings
        if settings.graph_selected_ig_only:
            return ["IG2" if self.selected_ig_var.get() == 2 else "IG1"]
        selected = [g for g in settings.graph_visible_gauges if g in GRAPH_GAUGE_KEYS]
        return selected or list(GRAPH_GAUGE_KEYS)

    def draw_pressure_graph(self, status: Optional[NGC3Status] = None):
        canvas = self.graph_canvas
        if canvas is None:
            return
        if status is None:
            status = self._latest_status
        self._last_graph_draw_at = time.monotonic()
        width = max(canvas.winfo_width(), 360)
        height = max(canvas.winfo_height(), GRAPH_CANVAS_HEIGHT)
        canvas.delete("all")
        canvas.configure(background=UNIBAS_WHITE, highlightbackground=UNIBAS_GRAY5)
        self._graph_hover_points = []

        plot_gauges = self._visible_graph_gauges()
        left = 100
        right = max(left + 220, width - 18)
        top = 28
        bottom = height - 60
        axis_color = UNIBAS_GRAY2
        grid_color = UNIBAS_GRAY6
        text_color = UNIBAS_GRAY1
        canvas.create_rectangle(left, top, right, bottom, outline=UNIBAS_GRAY5)

        try:
            series = pressure_logger.query_series(self.port, self._graph_range_seconds())
        except Exception as exc:
            canvas.create_text(width / 2, height / 2, text=f"Pressure log unavailable: {exc}", fill=axis_color)
            return

        # Add the latest live status as an unsaved point so the plot updates immediately.
        if status is not None and status.ok:
            live_ts = status.timestamp
            for key, gauge in status.gauges.items():
                if key not in plot_gauges:
                    continue
                p_mbar = gauge.pressure_mbar
                if p_mbar is not None and p_mbar > 0:
                    series.setdefault(key, []).append((live_ts, float(p_mbar)))

        series = {g: list(series.get(g, [])) for g in plot_gauges}
        all_points = [(t, p) for values in series.values() for t, p in values if p > 0]
        if not all_points:
            if alert_manager.settings.graph_selected_ig_only:
                which = plot_gauges[0] if plot_gauges else "selected gauge"
                canvas.create_text(width / 2, height / 2, text=f"No pressure data for {which}", fill=axis_color)
            else:
                canvas.create_text(width / 2, height / 2, text="No pressure data", fill=axis_color)
            return

        min_t = min(t for t, _ in all_points)
        max_t = max(t for t, _ in all_points)
        min_p = min(p for _, p in all_points)
        max_p = max(p for _, p in all_points)
        if min_t == max_t:
            min_t -= 60
            max_t += 60
        if min_p <= 0:
            min_p = min(p for _, p in all_points if p > 0)
        data_log_min = math.log10(min_p)
        data_log_max = math.log10(max_p)
        if data_log_min == data_log_max:
            log_min = data_log_min - 0.18
            log_max = data_log_max + 0.18
        else:
            log_span = data_log_max - data_log_min
            pad = max(0.05, min(0.35, log_span * 0.12))
            log_min = data_log_min - pad
            log_max = data_log_max + pad
            if log_max - log_min < 0.30:
                centre = (log_min + log_max) / 2.0
                log_min = centre - 0.15
                log_max = centre + 0.15

        def x_of(ts: float) -> float:
            return left + (ts - min_t) / (max_t - min_t) * (right - left)

        def y_of(p: float) -> float:
            return bottom - (math.log10(p) - log_min) / (log_max - log_min) * (bottom - top)

        # Y axis: pressure in mBar.  Use a tighter logarithmic range and 1/2/5
        # ticks so small pressure changes are easier to read than with decade-only
        # labels.
        try:
            canvas.create_text(22, (top + bottom) / 2, text="Pressure [mBar]", anchor="center", angle=90, fill=axis_color, font=FONT_CAPTION)
        except tk.TclError:
            canvas.create_text(8, (top + bottom) / 2, text="Pressure [mBar]", anchor="w", fill=axis_color, font=FONT_CAPTION)

        y_ticks: List[Tuple[float, bool]] = []
        for exp in range(math.floor(log_min) - 1, math.ceil(log_max) + 2):
            for coeff in (1, 2, 5):
                value = coeff * (10 ** exp)
                log_value = math.log10(value)
                if log_min <= log_value <= log_max:
                    y_ticks.append((float(value), coeff == 1))
        last_label_y: Optional[float] = None
        for value, is_decade in sorted(y_ticks):
            y = y_of(value)
            if not (top <= y <= bottom):
                continue
            if is_decade:
                canvas.create_line(left - 4, y, right, y, fill=grid_color)
            else:
                canvas.create_line(left - 4, y, right, y, fill=grid_color, dash=(2, 4))
            if last_label_y is None or abs(y - last_label_y) >= 15:
                label = f"{value:.0e}"
                canvas.create_text(left - 10, y, text=label, anchor="e", fill=axis_color, font=FONT_CAPTION)
                last_label_y = y

        # X axis: elapsed hours. Add the date when the displayed range crosses a date boundary.
        duration_h = max((max_t - min_t) / 3600.0, 1e-9)
        nticks = 5 if (right - left) < 520 else 7
        min_date = time.strftime("%Y-%m-%d", time.localtime(min_t))
        max_date = time.strftime("%Y-%m-%d", time.localtime(max_t))
        spans_dates = min_date != max_date
        for i in range(nticks):
            frac = i / (nticks - 1)
            ts = min_t + frac * (max_t - min_t)
            x = x_of(ts)
            canvas.create_line(x, top, x, bottom + 4, fill=grid_color)
            hour = frac * duration_h
            hour_label = f"{hour:.1f} h" if duration_h <= 2 else f"{hour:.0f} h"
            if spans_dates:
                label = f"{hour_label}\n{time.strftime('%d.%m', time.localtime(ts))}"
            else:
                label = hour_label
            canvas.create_text(x, bottom + 10, text=label, anchor="n", fill=axis_color, font=FONT_CAPTION)

        if spans_dates:
            first_midnight = time.mktime(time.strptime(time.strftime("%Y-%m-%d", time.localtime(min_t)), "%Y-%m-%d")) + 24 * 3600
            midnight = first_midnight
            while midnight < max_t:
                if midnight > min_t:
                    x = x_of(midnight)
                    canvas.create_line(x, top, x, bottom, fill=axis_color, dash=(2, 3))
                    canvas.create_text(x + 3, top + 3, text=time.strftime("%d.%m", time.localtime(midnight)), anchor="nw", fill=axis_color, font=FONT_CAPTION)
                midnight += 24 * 3600
        canvas.create_text((left + right) / 2, height - 6, text="Time [hours]", anchor="s", fill=axis_color, font=FONT_CAPTION)

        max_draw = max(50, int((right - left) * 1.5))
        legend_entries: List[Tuple[str, str]] = []
        for gauge in plot_gauges:
            values = sorted(series.get(gauge, []))
            values = [(t, p) for t, p in values if p > 0]
            if not values:
                continue
            step = max(1, len(values) // max_draw)
            values = values[::step]
            try:
                color = GRAPH_COLORS[GRAPH_GAUGE_KEYS.index(gauge) % len(GRAPH_COLORS)]
            except ValueError:
                color = GRAPH_COLORS[0]
            coords: List[float] = []
            drawn_points: List[Tuple[float, float, float, float]] = []
            for ts, pressure in values:
                x = x_of(ts)
                y = y_of(pressure)
                coords.extend([x, y])
                drawn_points.append((x, y, ts, pressure))
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=2)
            for x, y, ts, pressure in drawn_points:
                radius = 2.4
                canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline=color, fill=color)
                self._graph_hover_points.append((x, y, gauge, ts, pressure, color))
            legend_entries.append((gauge, color))

        if legend_entries:
            legend_w = min(138, max(86, right - left - 18))
            row_h = 16
            pad_x = 6
            pad_y = 6
            legend_h = row_h * len(legend_entries)
            legend_x = left + 10
            legend_y = max(top + pad_y + 2, bottom - legend_h - pad_y)
            canvas.create_rectangle(
                legend_x - pad_x,
                legend_y - pad_y,
                legend_x + legend_w,
                legend_y + legend_h - row_h + pad_y,
                fill=UNIBAS_WHITE,
                outline=UNIBAS_GRAY5,
            )
            y = legend_y
            for gauge, color in legend_entries:
                canvas.create_line(legend_x, y, legend_x + 18, y, fill=color, width=2)
                canvas.create_text(legend_x + 24, y, text=gauge, anchor="w", fill=text_color, font=FONT_CAPTION)
                y += row_h

    def _clear_graph_hover(self, _event=None):
        canvas = self.graph_canvas
        if canvas is not None:
            canvas.delete("graph_hover")

    def _on_graph_motion(self, event):
        canvas = self.graph_canvas
        if canvas is None or not self._graph_hover_points:
            return
        closest = min(
            self._graph_hover_points,
            key=lambda point: (point[0] - event.x) ** 2 + (point[1] - event.y) ** 2,
        )
        x, y, gauge, ts, pressure, color = closest
        if (x - event.x) ** 2 + (y - event.y) ** 2 > 18 ** 2:
            self._clear_graph_hover()
            return

        canvas.delete("graph_hover")
        canvas.create_oval(x - 5, y - 5, x + 5, y + 5, outline=color, width=2, tags="graph_hover")
        label = f"{gauge}\n{pressure:.3e} mBar\n{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}"
        width = max(canvas.winfo_width(), 360)
        height = max(canvas.winfo_height(), GRAPH_CANVAS_HEIGHT)
        tx = x + 12
        ty = y - 48
        if tx + 175 > width:
            tx = x - 175
        if ty < 8:
            ty = y + 12
        if ty + 52 > height:
            ty = max(8, height - 56)
        text_id = canvas.create_text(tx, ty, text=label, anchor="nw", fill=UNIBAS_GRAY1, font=FONT_CAPTION, tags="graph_hover")
        bbox = canvas.bbox(text_id)
        if bbox:
            rect_id = canvas.create_rectangle(
                bbox[0] - 5, bbox[1] - 4, bbox[2] + 5, bbox[3] + 4,
                fill=UNIBAS_WHITE, outline=UNIBAS_GRAY5, tags="graph_hover",
            )
            canvas.tag_lower(rect_id, text_id)

    def update_pressure_history(self, status: NGC3Status):
        now = time.time()
        max_age = max(60, alert_manager.settings.pressure_rise_window_seconds * 3)
        for key, gauge in status.gauges.items():
            p = gauge.pressure_mbar
            if p is None or p <= 0:
                continue
            hist = self.pressure_history.setdefault(key, [])
            hist.append((now, p))
            self.pressure_history[key] = [(t, val) for t, val in hist if now - t <= max_age]

    def update_auto_recommendation(self, status: NGC3Status):
        self.recommendation = None
        self.auto_hint_var.set("")
        settings = alert_manager.settings
        sensitivity = safe_float(settings.ig_sensitivity_mbar_inv) or DEFAULT_IG_SENSITIVITY_MBAR_INV
        up_threshold, down_threshold = emission_thresholds_mbar(sensitivity)
        if up_threshold is None or down_threshold is None:
            return
        ig_name = status.selected_ig
        gauge = status.gauges.get(ig_name)
        if not gauge or not gauge.is_emitting or gauge.pressure_mbar is None:
            return
        p_mbar = gauge.pressure_mbar
        current = self.device.last_emission_request
        if current == "0.5mA" and p_mbar <= up_threshold:
            msg = f"{ig_name}: {p_mbar:.2e} mBar < {up_threshold:.2e} mBar."
            if self.device.software_auto:
                if alert_manager.settings.remote_control_enabled:
                    self.auto_hint_var.set("AUTO: increasing to 5 mA. " + msg)
                    self._auto_switch("increase", lambda: self.device.gauge_on_5ma(self.selected_ig_var.get()))
                else:
                    self.auto_hint_var.set("AUTO: reader mode. " + msg)
            else:
                self.recommendation = "increase_5mA"
                self.auto_hint_var.set("Suggest 5 mA. " + msg)
        elif current == "5mA" and p_mbar >= down_threshold:
            msg = f"{ig_name}: {p_mbar:.2e} mBar > {down_threshold:.2e} mBar."
            if self.device.software_auto:
                if alert_manager.settings.remote_control_enabled:
                    self.auto_hint_var.set("AUTO: decreasing to 0.5 mA. " + msg)
                    self._auto_switch("decrease", lambda: self.device.gauge_on_05ma(self.selected_ig_var.get()))
                else:
                    self.auto_hint_var.set("AUTO: reader mode. " + msg)
            else:
                self.recommendation = "decrease_05mA"
                self.auto_hint_var.set("Suggest 0.5 mA. " + msg)
        elif self.device.software_auto:
            self.auto_hint_var.set(f"AUTO: 5 mA < {up_threshold:.2e}; 0.5 mA > {down_threshold:.2e} mBar.")

    def _auto_switch(self, key: str, func):
        now = time.time()
        if now - self._last_auto_action.get(key, 0) < 10:
            return
        self._last_auto_action[key] = now
        self._run_command(f"AUTO {key}", func)

    def evaluate_and_publish_alerts(self, status: NGC3Status):
        candidates = self.evaluate_alerts(status)
        alert_manager.update_panel_alerts(self, candidates)
        self.app.refresh_alert_summary()

    def evaluate_alerts(self, status: NGC3Status) -> List[Tuple[str, str, str, str]]:
        alerts: List[Tuple[str, str, str, str]] = []
        settings = alert_manager.settings
        if not status.ok:
            alerts.append(("status_parse", "critical", "Could not parse controller status", status.parse_error))
            return alerts
        if status.state_byte & (1 << 7):
            alerts.append(("ig_disconnected", "critical", "Ion gauge disconnected", "Instrument state byte bit 7 is set."))
        if status.error_byte & (1 << 0):
            alerts.append(("instrument_gauge_error", "warning", "Gauge-specific error reported", "Check the per-gauge error flags."))
        if status.error_byte & (1 << 1):
            alerts.append(("over_temperature_trip", "critical", "Over-temperature trip", "The controller reports an over-temperature trip."))
        if status.error_byte & (1 << 2):
            alerts.append(("bake_error", "warning", "Bake error", "The controller reports a bake error."))
        if status.error_byte & (1 << 3):
            alerts.append(("temperature_warning", "warning", "Temperature warning", "The controller reports a temperature warning."))

        for key, gauge in status.gauges.items():
            if gauge.gauge_type == "I":
                if gauge.error_byte & (1 << 0):
                    alerts.append((f"{key}_filament_open", "critical", f"{key}: filament open-circuit", "Likely broken filament or open filament wiring."))
                if gauge.error_byte & (1 << 1):
                    alerts.append((f"{key}_overemission", "critical", f"{key}: overemission", "Emission current regulation problem reported."))
                if gauge.error_byte & (1 << 2):
                    alerts.append((f"{key}_underemission", "warning", f"{key}: underemission", "Could indicate filament, pressure, or emission regulation problem."))
                if gauge.error_byte & (1 << 3):
                    alerts.append((f"{key}_overpressure", "critical", f"{key}: overpressure", "The controller reports ion-gauge overpressure."))
                if gauge.error_byte & (1 << 4):
                    alerts.append((f"{key}_interlock", "warning", f"{key}: interlock/autostart prevents starting", "The controller prevented emission because an interlock/autostart condition was not satisfied."))
                if gauge.error_byte & (1 << 7):
                    alerts.append((f"{key}_filament_leads", "critical", f"{key}: filament/leads error", "Check gauge head and cable wiring."))
            elif gauge.gauge_type == "P":
                if gauge.error_byte & (1 << 0):
                    alerts.append((f"{key}_pirani_open", "warning", f"{key}: Pirani open-circuit", "Pirani gauge or cable may be disconnected."))

            p_mbar = gauge.pressure_mbar
            if p_mbar is not None:
                if gauge.gauge_type == "I":
                    threshold = safe_float(settings.ig_pressure_critical_mbar)
                    if threshold is not None and p_mbar >= threshold:
                        sev = "critical" if gauge.is_emitting else "warning"
                        details = f"Pressure is {p_mbar:.3e} mBar; threshold is {threshold:.3e} mBar."
                        if gauge.is_emitting:
                            details += " Ion gauge is in emission."
                        alerts.append((f"{key}_high_pressure", sev, f"{key}: pressure above threshold", details))
                elif gauge.gauge_type == "P":
                    threshold = safe_float(settings.pirani_pressure_warning_mbar)
                    if threshold is not None and p_mbar >= threshold:
                        alerts.append((f"{key}_high_pressure", "warning", f"{key}: Pirani pressure above threshold", f"Pressure is {p_mbar:.3e} mBar; threshold is {threshold:.3e} mBar."))
                else:
                    threshold = safe_float(settings.active_pressure_warning_mbar)
                    if threshold is not None and p_mbar >= threshold:
                        alerts.append((f"{key}_high_pressure", "warning", f"{key}: active gauge pressure above threshold", f"Pressure is {p_mbar:.3e} mBar; threshold is {threshold:.3e} mBar."))

        if settings.pressure_rise_enabled:
            alerts.extend(self.evaluate_pressure_rise())
        return alerts

    def evaluate_pressure_rise(self) -> List[Tuple[str, str, str, str]]:
        settings = alert_manager.settings
        alerts: List[Tuple[str, str, str, str]] = []
        window = max(5, settings.pressure_rise_window_seconds)
        factor = max(1.1, float(settings.pressure_rise_factor))
        now = time.time()
        for key, hist in list(self.pressure_history.items()):
            recent = [(t, p) for t, p in hist if now - t <= window]
            if len(recent) < 3:
                continue
            oldest = recent[0][1]
            newest = recent[-1][1]
            if oldest > 0 and newest / oldest >= factor:
                alerts.append((f"{key}_rapid_rise", "warning", f"{key}: pressure rising fast", f"Pressure rose from {oldest:.3e} to {newest:.3e} mBar within {window} s."))
        return alerts

    def _button_text_for_bg(self, bg: str) -> str:
        return _readable_fg_for_bg(bg)

    def _configure_command_button(self, button: Optional[ColorButton], *, bg: str, fg: Optional[str] = None, enabled: bool = True):
        if not button:
            return
        shown_bg = bg if enabled else BTN_DISABLED_BG
        shown_fg = self._button_text_for_bg(shown_bg) if enabled else ("#D2EBE9" if CURRENT_DARK_MODE else UNIBAS_GRAY3)
        if fg is not None and enabled:
            # Keep explicit white for OFF and other high-contrast cases, but avoid unreadable dark-on-dark.
            explicit = fg
            if CURRENT_DARK_MODE and explicit in (UNIBAS_ANTHRAZIT, "#2D373C", "#333333") and shown_bg in (BTN_BG, "#20282D", "#151A1D", BTN_PROMPT_ORANGE):
                explicit = "#FFFFFF"
            shown_fg = explicit
        button.config(
            state=tk.NORMAL if enabled else tk.DISABLED,
            bg=shown_bg,
            fg=shown_fg,
            activebackground=UNIBAS_MINT_LIGHT if enabled and shown_bg == UNIBAS_WHITE else shown_bg,
            activeforeground=shown_fg,
            disabledforeground=shown_fg,
            highlightbackground=shown_bg,
            highlightthickness=0,
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=8,
            font=FONT_BUTTON,
            cursor="hand2" if enabled else "",
        )

    def _force_off_button_red(self):
        """Keep OFF visibly red even when the device is not connected.

        Some Tk/macOS themes repaint buttons after a failed serial connection or after
        a theme refresh.  This method reapplies the OFF button colour last and keeps
        the button in NORMAL state so the red background is actually drawn.
        """
        if not self.btn_off:
            return
        off_fg = _readable_fg_for_bg(BTN_OFF_RED)
        self.btn_off.config(
            state=tk.NORMAL,
            bg=BTN_OFF_RED,
            fg=off_fg,
            activebackground=BTN_OFF_RED,
            activeforeground=off_fg,
            disabledforeground=off_fg,
            highlightbackground=BTN_OFF_RED,
            highlightthickness=0,
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=8,
            font=FONT_BUTTON,
            cursor="hand2",
        )

    def update_button_states(self):
        req = self.device.last_emission_request
        unlocked = self.device.five_ma_unlocked
        self._blink_state = not self._blink_state
        prompt_color = BTN_PROMPT_ORANGE if self._blink_state else BTN_BG

        self._configure_command_button(
            self.btn_05,
            bg=BTN_SELECTED_GREEN if req == "0.5mA" else (prompt_color if self.recommendation == "decrease_05mA" else BTN_BG),
            fg=BTN_SELECTED_FG if req == "0.5mA" else BTN_FG,
            enabled=True,
        )
        self._configure_command_button(
            self.btn_5,
            bg=BTN_SELECTED_GREEN if req == "5mA" else (prompt_color if self.recommendation == "increase_5mA" else BTN_BG),
            fg=BTN_SELECTED_FG if req == "5mA" else BTN_FG,
            enabled=unlocked or self.recommendation == "increase_5mA",
        )
        self._configure_command_button(
            self.btn_auto,
            bg=BTN_AUTO_BLUE if self.device.software_auto else BTN_BG,
            fg=BTN_SELECTED_FG if self.device.software_auto else BTN_FG,
            enabled=True,
        )
        self._configure_command_button(self.btn_off, bg=BTN_OFF_RED, fg=_readable_fg_for_bg(BTN_OFF_RED), enabled=True)
        self._force_off_button_red()
        if self.recommendation:
            if self._blink_after is None:
                self._blink_after = self.after(650, self._run_blink_update)
        elif self._blink_after is not None:
            try:
                self.after_cancel(self._blink_after)
            except Exception:
                pass
            self._blink_after = None

    def _run_blink_update(self) -> None:
        self._blink_after = None
        if self.recommendation and self.winfo_exists():
            self.update_button_states()

    def set_error(self, text: str):
        self.error_var.set(f"Error: {text}")
        self.app.web_monitor.update_error(self, text)
        now = time.time()
        if now - self._last_error_at >= max(1, alert_manager.settings.no_response_seconds):
            self._last_error_at = now
            alert_manager.raise_alert(self.display_name, f"{self.port}:no_response", "critical", "No serial response / communication error", text)
            self.app.refresh_alert_summary()

    def _remote_control_allowed(self, action: str) -> bool:
        if alert_manager.settings.remote_control_enabled:
            return True
        messagebox.showinfo("Reader mode", f"Remote disabled in Options. '{action}' not sent.")
        return False

    def _run_command(self, title: str, func):
        def worker():
            try:
                func()
                status = self.device.read_status()
                self.app.events.put(("status", self, status))
                self.app.events.put(("save", self, None))
            except Exception as exc:
                self.app.events.put(("error", self, f"{title}: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def on_start_05(self):
        if not self._remote_control_allowed("0.5 mA"):
            return
        ig = self.selected_ig_var.get()
        self.device.last_emission_request = "0.5mA"
        self.device.five_ma_unlocked = True
        self.recommendation = None
        self.update_button_states()
        self.app.save_config()
        self._run_command("0.5 mA", lambda: self.device.gauge_on_05ma(ig))

    def on_start_5(self):
        if not self._remote_control_allowed("5 mA"):
            return
        ig = self.selected_ig_var.get()
        if not self.device.five_ma_unlocked and self.recommendation != "increase_5mA":
            messagebox.showwarning("5 mA locked", "Start at 0.5 mA first for this controller panel.")
            return
        self.device.five_ma_unlocked = True
        self.device.last_emission_request = "5mA"
        self.recommendation = None
        self.update_button_states()
        self.app.save_config()
        self._run_command("5 mA", lambda: self.device.gauge_on_5ma(ig))

    def on_toggle_auto(self):
        enable = not self.device.software_auto
        self.device.software_auto = enable
        if not alert_manager.settings.remote_control_enabled:
            self.auto_hint_var.set("AUTO: reader mode.")
            self.recommendation = None
            self.update_button_states()
            self.app.save_config()
            return
        if enable and self.device.last_emission_request == "off":
            if messagebox.askyesno("Enable AUTO", "Start selected gauge at 0.5 mA?"):
                self.device.last_emission_request = "0.5mA"
                self.device.five_ma_unlocked = True
                self._run_command("AUTO start 0.5 mA", lambda: self.device.gauge_on_05ma(self.selected_ig_var.get()))
            else:
                self.device.software_auto = False
        self.recommendation = None
        self.update_button_states()
        self.app.save_config()

    def on_off(self):
        if not self._remote_control_allowed("OFF"):
            return
        self.device.last_emission_request = "off"
        self.device.five_ma_unlocked = False
        self.device.software_auto = False
        self.recommendation = None
        self.update_button_states()
        self.app.save_config()
        self._run_command("Gauge off", self.device.gauge_off)

    def on_reset_errors(self):
        if not self._remote_control_allowed("Reset errors"):
            return
        self._run_command("Reset errors", self.device.reset_error)

    def on_release_local(self):
        if not self._remote_control_allowed("Release local"):
            return
        if messagebox.askyesno(
            "Release local control",
            "Release local control?",
        ):
            self.device.last_emission_request = "off"
            self.device.five_ma_unlocked = False
            self.device.software_auto = False
            self.recommendation = None
            self.update_button_states()
            self.app.save_config()
            self._run_command("Release local", self.device.release_local)

    def remove(self):
        self.stop_event.set()
        for after_id in (self._graph_redraw_after, self._blink_after):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
        self._graph_redraw_after = None
        self._blink_after = None
        try:
            self.device.close()
        finally:
            self.app.web_monitor.remove_panel(self)
            self.app.remove_panel(self)





class PfeifferDualGaugePanel(ttk.LabelFrame):
    """Read-only panel for a Pfeiffer DualGauge TPG 262."""

    device_type = PFEIFFER_DEVICE_TYPE
    gauge_keys = PFEIFFER_GAUGE_KEYS
    panel_heading = "Pfeiffer DualGauge pressures"

    def make_device(self, port, baudrate, saved_state):
        return PfeifferDualGaugeDevice(port=port, baudrate=baudrate)

    def gauge_status_label(self, code):
        return PFEIFFER_STATUS_LABELS.get(code, f"Status {code}")

    def __init__(self, parent, app: "UHVSentinelApp", name: str, port: str, baudrate: int = 9600,
                 saved_state: Optional[Dict[str, Any]] = None):
        super().__init__(parent, text=name, style="Card.TLabelframe")
        self.app = app
        self.name_var = tk.StringVar(value=name)
        self.port = port
        saved_state = saved_state or {}
        self.device = self.make_device(port, baudrate, saved_state)
        graph_range = saved_state.get("graph_range", alert_manager.settings.graph_default_range)
        if graph_range not in GRAPH_RANGE_OPTIONS:
            graph_range = "24 h"
        self.graph_range_var = tk.StringVar(value=graph_range)
        self._latest_status: Optional[PfeifferStatus] = None
        self._last_graph_draw_at = 0.0
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.connection_var = tk.StringVar(value="Disconnected")
        self.alert_summary_var = tk.StringVar(value="Alerts: none")
        self.pressure_vars = {key: tk.StringVar(value="---") for key in self.gauge_keys}
        self.channel_label_vars = {
            key: tk.StringVar(value=key) for key in self.gauge_keys
        }
        self.unit_var = tk.StringVar(value="Unit: ---")
        self.status_var = tk.StringVar(value="Channel status: ---")
        self.last_update_var = tk.StringVar(value="Last update: ---")
        self.raw_var = tk.StringVar(value="Raw: ---")
        self.diagnostics_var = tk.StringVar(value="")
        self.error_var = tk.StringVar(value="")
        self.log_status_var = tk.StringVar(value="Last log: —")
        self.last_logged_at = 0.0
        self._last_error_at = 0.0
        self.graph_canvas: Optional[tk.Canvas] = None
        self._graph_redraw_after: Optional[str] = None
        self._graph_hover_points: List[Tuple[float, float, str, float, float, str]] = []
        self._build()
        self._connect()

    @property
    def display_name(self) -> str:
        return self.name_var.get().strip() or self.port

    def state_for_config(self) -> Dict[str, Any]:
        state = {"graph_range": self.graph_range_var.get()}
        if self.device_type == EPIMAX_DEVICE_TYPE:
            state.update(address=self.device.address, pressure_units=self.device.pressure_units)
        return state

    def _build(self) -> None:
        try:
            self.configure(width=PANEL_MIN_WIDTH)
        except Exception:
            pass
        self.columnconfigure(0, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text=f"Port: {self.port}").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.connection_var).grid(row=0, column=1, sticky="e", padx=(8, 8))
        ttk.Button(top, text="Remove panel", command=self.remove).grid(row=0, column=2, sticky="e")

        alert_box = ttk.LabelFrame(self, text="Alerts")
        alert_box.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        alert_box.columnconfigure(0, weight=1)
        ttk.Label(alert_box, textvariable=self.alert_summary_var, wraplength=820, justify="left").grid(
            row=0, column=0, sticky="ew", padx=4, pady=2
        )

        pressures = ttk.LabelFrame(self, text=self.panel_heading)
        pressures.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        pressures.columnconfigure(1, weight=1)
        for row, key in enumerate(self.gauge_keys):
            ttk.Label(pressures, textvariable=self.channel_label_vars[key]).grid(row=row, column=0, sticky="w", padx=4, pady=2)
            ttk.Label(pressures, textvariable=self.pressure_vars[key], font=FONT_H6).grid(row=row, column=1, sticky="e", padx=4, pady=2)

        graph = ttk.LabelFrame(self, text="Pressure graph")
        graph.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        graph.columnconfigure(2, weight=1)
        ttk.Label(graph, text="Range:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        range_combo = ttk.Combobox(graph, textvariable=self.graph_range_var, values=GRAPH_RANGE_OPTIONS, width=8, state="readonly")
        range_combo.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        range_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_graph_range_changed())
        ttk.Label(graph, textvariable=self.log_status_var).grid(row=0, column=2, sticky="e", padx=4, pady=2)
        self.graph_canvas = tk.Canvas(
            graph,
            width=GRAPH_CANVAS_WIDTH,
            height=GRAPH_CANVAS_HEIGHT,
            background=UNIBAS_WHITE,
            highlightthickness=1,
            highlightbackground=UNIBAS_GRAY5,
        )
        self.graph_canvas.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 4))
        self.graph_canvas.bind("<Configure>", lambda _event: self.schedule_graph_redraw())
        self.graph_canvas.bind("<Motion>", self._on_graph_motion)
        self.graph_canvas.bind("<Leave>", self._clear_graph_hover)

        info = ttk.LabelFrame(self, text="Status")
        info.grid(row=4, column=0, sticky="ew", padx=8, pady=(4, 8))
        info.columnconfigure(0, weight=1)
        for row, var in enumerate((self.unit_var, self.status_var, self.last_update_var, self.raw_var,
                                   self.error_var, self.diagnostics_var)):
            ttk.Label(info, textvariable=var, wraplength=820, justify="left").grid(row=row, column=0, sticky="ew", padx=4, pady=1)

    def _connect(self) -> None:
        try:
            self.device.open()
            self.connection_var.set("Connected")
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._poll_loop, name=f"Pfeiffer-{self.port}", daemon=True)
            self.thread.start()
        except Exception as exc:
            self.connection_var.set("Connection failed")
            self.set_error(str(exc))
            self.after(100, self.draw_pressure_graph)

    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                status = self.device.read_status()
                self.app.events.put(("status", self, status))
            except Exception as exc:
                self.app.events.put(("error", self, str(exc)))
            self.stop_event.wait(POLL_INTERVAL_S)

    def update_status(self, status: PfeifferStatus) -> None:
        self._latest_status = status
        self.app.web_monitor.update_panel(self, status)
        if not status.ok:
            self.error_var.set(f"Status parse error: {status.parse_error}")
            self.raw_var.set(f"Raw: {status.raw}")
            self.evaluate_and_publish_alerts(status)
            return

        self.connection_var.set("Connected")
        self.error_var.set("")
        for key in self.gauge_keys:
            gauge = status.gauges.get(key)
            if gauge is None:
                self.pressure_vars[key].set("---")
                continue
            sensor = gauge.sensor_id or "unknown sensor"
            self.channel_label_vars[key].set(f"{key} ({sensor})")
            self.pressure_vars[key].set(gauge.pressure_display)
        self.unit_var.set(f"Unit: {status.units}")
        status_parts = []
        for key in self.gauge_keys:
            gauge = status.gauges.get(key)
            if gauge is not None:
                status_parts.append(f"{key}: {self.gauge_status_label(gauge.status_code)}")
        self.status_var.set("Channel status: " + " | ".join(status_parts))
        self.last_update_var.set("Last update: " + time.strftime("%H:%M:%S"))
        self.raw_var.set(f"Raw: {status.raw}")
        diagnostics = getattr(status, "diagnostics", {})
        self.diagnostics_var.set("\n".join(f"{k}: {v}" for k, v in diagnostics.items()))
        pressure_logger.maybe_log_status(self, status)
        self.request_graph_redraw(status)
        self.evaluate_and_publish_alerts(status)
        self.app.request_save_config()

    def evaluate_and_publish_alerts(self, status: PfeifferStatus) -> None:
        candidates: List[Tuple[str, str, str, str]] = []
        if not status.ok:
            candidates.append(("status_parse", "critical", "Could not parse instrument pressure response", status.parse_error))
        elif self.device_type == PFEIFFER_DEVICE_TYPE:
            for key, gauge in status.gauges.items():
                if gauge.status_code == 3:
                    candidates.append((f"{key}_sensor_error", "critical", f"{key}: sensor error", "The TPG 262 reports sensor status 3."))
                elif gauge.status_code == 6:
                    candidates.append((f"{key}_identification_error", "warning", f"{key}: identification error", "The TPG 262 could not identify the connected gauge."))
        alert_manager.update_panel_alerts(self, candidates)
        self.app.refresh_alert_summary()

    def set_error(self, text: str) -> None:
        self.connection_var.set("Communication error")
        self.error_var.set(f"Error: {text}")
        self.app.web_monitor.update_error(self, text)
        now = time.time()
        if now - self._last_error_at >= max(1, alert_manager.settings.no_response_seconds):
            self._last_error_at = now
            alert_manager.raise_alert(
                self.display_name,
                f"{self.port}:no_response",
                "critical",
                "No instrument response / communication error",
                text,
            )
            self.app.refresh_alert_summary()

    def on_graph_range_changed(self) -> None:
        self.app.save_config()
        self.request_graph_redraw(force=True)

    def _graph_range_seconds(self) -> Optional[int]:
        return GRAPH_RANGE_SECONDS.get(self.graph_range_var.get(), 24 * 3600)

    def request_graph_redraw(self, status: Optional[PfeifferStatus] = None, *, force: bool = False) -> None:
        if status is not None:
            self._latest_status = status
        elapsed = time.monotonic() - self._last_graph_draw_at
        if force or elapsed >= GRAPH_REFRESH_INTERVAL_S:
            if self._graph_redraw_after is not None:
                try:
                    self.after_cancel(self._graph_redraw_after)
                except Exception:
                    pass
                self._graph_redraw_after = None
            self.draw_pressure_graph(self._latest_status)
            return
        if self._graph_redraw_after is None:
            delay_ms = max(120, int((GRAPH_REFRESH_INTERVAL_S - elapsed) * 1000))
            self._graph_redraw_after = self.after(delay_ms, self._run_scheduled_graph_redraw)

    def schedule_graph_redraw(self) -> None:
        self.request_graph_redraw(force=False)

    def _run_scheduled_graph_redraw(self) -> None:
        self._graph_redraw_after = None
        self.draw_pressure_graph(self._latest_status)

    def draw_pressure_graph(self, status: Optional[PfeifferStatus] = None) -> None:
        canvas = self.graph_canvas
        if canvas is None:
            return
        if status is None:
            status = self._latest_status
        self._last_graph_draw_at = time.monotonic()
        width = max(canvas.winfo_width(), 360)
        height = max(canvas.winfo_height(), GRAPH_CANVAS_HEIGHT)
        canvas.delete("all")
        canvas.configure(background=UNIBAS_WHITE, highlightbackground=UNIBAS_GRAY5)
        self._graph_hover_points = []

        left = 100
        right = max(left + 220, width - 18)
        top = 28
        bottom = height - 60
        axis_color = UNIBAS_GRAY2
        grid_color = UNIBAS_GRAY6
        canvas.create_rectangle(left, top, right, bottom, outline=UNIBAS_GRAY5)

        try:
            stored = pressure_logger.query_series(self.port, self._graph_range_seconds())
        except Exception as exc:
            canvas.create_text(width / 2, height / 2, text=f"Pressure log unavailable: {exc}", fill=axis_color)
            return
        series = {key: list(stored.get(key, [])) for key in self.gauge_keys}
        if status is not None and status.ok:
            for key, gauge in status.gauges.items():
                p_mbar = gauge.pressure_mbar
                if key in series and p_mbar is not None and p_mbar > 0:
                    series[key].append((status.timestamp, p_mbar))

        all_points = [(ts, pressure) for values in series.values() for ts, pressure in values if pressure > 0]
        if not all_points:
            canvas.create_text(width / 2, height / 2, text="No pressure data", fill=axis_color)
            return

        min_t = min(ts for ts, _pressure in all_points)
        max_t = max(ts for ts, _pressure in all_points)
        min_p = min(pressure for _ts, pressure in all_points)
        max_p = max(pressure for _ts, pressure in all_points)
        if min_t == max_t:
            min_t -= 60
            max_t += 60
        log_min_data = math.log10(min_p)
        log_max_data = math.log10(max_p)
        if log_min_data == log_max_data:
            log_min, log_max = log_min_data - 0.18, log_max_data + 0.18
        else:
            span = log_max_data - log_min_data
            pad = max(0.05, min(0.35, span * 0.12))
            log_min, log_max = log_min_data - pad, log_max_data + pad
            if log_max - log_min < 0.30:
                centre = (log_min + log_max) / 2.0
                log_min, log_max = centre - 0.15, centre + 0.15

        def x_of(ts: float) -> float:
            return left + (ts - min_t) / (max_t - min_t) * (right - left)

        def y_of(pressure: float) -> float:
            return bottom - (math.log10(pressure) - log_min) / (log_max - log_min) * (bottom - top)

        try:
            canvas.create_text(22, (top + bottom) / 2, text="Pressure [mBar]", anchor="center", angle=90, fill=axis_color, font=FONT_CAPTION)
        except tk.TclError:
            canvas.create_text(8, (top + bottom) / 2, text="Pressure [mBar]", anchor="w", fill=axis_color, font=FONT_CAPTION)

        last_label_y: Optional[float] = None
        ticks: List[Tuple[float, bool]] = []
        for exponent in range(math.floor(log_min) - 1, math.ceil(log_max) + 2):
            for coefficient in (1, 2, 5):
                value = coefficient * (10 ** exponent)
                if log_min <= math.log10(value) <= log_max:
                    ticks.append((float(value), coefficient == 1))
        for value, is_decade in sorted(ticks):
            y = y_of(value)
            if not (top <= y <= bottom):
                continue
            canvas.create_line(left - 4, y, right, y, fill=grid_color, dash=() if is_decade else (2, 4))
            if last_label_y is None or abs(y - last_label_y) >= 15:
                canvas.create_text(left - 10, y, text=f"{value:.0e}", anchor="e", fill=axis_color, font=FONT_CAPTION)
                last_label_y = y

        duration_h = max((max_t - min_t) / 3600.0, 1e-9)
        tick_count = 5 if (right - left) < 520 else 7
        spans_dates = time.strftime("%Y-%m-%d", time.localtime(min_t)) != time.strftime("%Y-%m-%d", time.localtime(max_t))
        for index in range(tick_count):
            fraction = index / (tick_count - 1)
            ts = min_t + fraction * (max_t - min_t)
            x = x_of(ts)
            canvas.create_line(x, top, x, bottom + 4, fill=grid_color)
            hour = fraction * duration_h
            label = f"{hour:.1f} h" if duration_h <= 2 else f"{hour:.0f} h"
            if spans_dates:
                label += "\n" + time.strftime("%d.%m", time.localtime(ts))
            canvas.create_text(x, bottom + 10, text=label, anchor="n", fill=axis_color, font=FONT_CAPTION)
        canvas.create_text((left + right) / 2, height - 6, text="Time [hours]", anchor="s", fill=axis_color, font=FONT_CAPTION)

        legend_entries: List[Tuple[str, str]] = []
        for index, key in enumerate(self.gauge_keys):
            values = sorted((ts, pressure) for ts, pressure in series.get(key, []) if pressure > 0)
            if not values:
                continue
            max_draw = max(50, int((right - left) * 1.5))
            step = max(1, len(values) // max_draw)
            values = values[::step]
            color = GRAPH_COLORS[(index + 1) % len(GRAPH_COLORS)]
            coords: List[float] = []
            points = []
            for ts, pressure in values:
                x, y = x_of(ts), y_of(pressure)
                coords.extend((x, y))
                points.append((x, y, ts, pressure))
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=2)
            for x, y, ts, pressure in points:
                canvas.create_oval(x - 2.4, y - 2.4, x + 2.4, y + 2.4, outline=color, fill=color)
                self._graph_hover_points.append((x, y, key, ts, pressure, color))
            legend_entries.append((key, color))

        if legend_entries:
            legend_x = left + 10
            legend_y = max(top + 8, bottom - 16 * len(legend_entries) - 6)
            canvas.create_rectangle(legend_x - 6, legend_y - 6, legend_x + 132, legend_y + 16 * len(legend_entries) - 10, fill=UNIBAS_WHITE, outline=UNIBAS_GRAY5)
            for index, (key, color) in enumerate(legend_entries):
                y = legend_y + 16 * index
                canvas.create_line(legend_x, y, legend_x + 18, y, fill=color, width=2)
                canvas.create_text(legend_x + 24, y, text=key, anchor="w", fill=UNIBAS_GRAY1, font=FONT_CAPTION)

    def _clear_graph_hover(self, _event=None) -> None:
        if self.graph_canvas is not None:
            self.graph_canvas.delete("graph_hover")

    def _on_graph_motion(self, event) -> None:
        canvas = self.graph_canvas
        if canvas is None or not self._graph_hover_points:
            return
        closest = min(self._graph_hover_points, key=lambda point: (point[0] - event.x) ** 2 + (point[1] - event.y) ** 2)
        x, y, key, ts, pressure, color = closest
        if (x - event.x) ** 2 + (y - event.y) ** 2 > 18 ** 2:
            self._clear_graph_hover()
            return
        self._clear_graph_hover()
        canvas.create_oval(x - 5, y - 5, x + 5, y + 5, outline=color, width=2, tags="graph_hover")
        label = f"{key}\n{pressure:.4e} mBar\n{now_human(ts)}"
        text_id = canvas.create_text(x + 10, y - 10, text=label, anchor="sw", fill=UNIBAS_GRAY1, font=FONT_CAPTION, tags="graph_hover")
        bbox = canvas.bbox(text_id)
        if bbox:
            pad = 4
            rect_id = canvas.create_rectangle(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad, fill=UNIBAS_WHITE, outline=UNIBAS_GRAY5, tags="graph_hover")
            canvas.tag_lower(rect_id, text_id)

    # No-op compatibility methods used by the common application event/theme code.
    def refresh_remote_control_visibility(self) -> None:
        return

    def update_button_states(self) -> None:
        return

    def _force_off_button_red(self) -> None:
        return

    def remove(self) -> None:
        self.stop_event.set()
        if self._graph_redraw_after is not None:
            try:
                self.after_cancel(self._graph_redraw_after)
            except Exception:
                pass
            self._graph_redraw_after = None
        try:
            self.device.close()
        finally:
            self.app.web_monitor.remove_panel(self)
            self.app.remove_panel(self)


class Agilent4UHVPanel(PfeifferDualGaugePanel):
    """Read-only Agilent 4UHV 2x80W NEG diagnostic and pressure dashboard."""
    device_type = AGILENT_DEVICE_TYPE
    gauge_keys = ["Channel 1", "Channel 2"]
    panel_heading = "Agilent 4UHV ion-pump pressures"

    def make_device(self, port, baudrate, saved_state):
        return Agilent4UHVDevice(port, baudrate)

    def gauge_status_label(self, code):
        return "Pressure available" if code == 0 else "Not available"


class EpimaxPVCPanel(PfeifferDualGaugePanel):
    """PVCi gauge 1 only; RJ45 serial Modbus RTU, not Ethernet TCP/IP."""
    device_type = EPIMAX_DEVICE_TYPE
    gauge_keys = ["Ion gauge 1"]
    panel_heading = "EpiMax PVCi ion-gauge pressure"

    def make_device(self, port, baudrate, saved_state):
        return EpimaxPVCDevice(port, baudrate, address=int(saved_state.get("address", 1)),
                               pressure_units=saved_state.get("pressure_units", "mBar"))

    def gauge_status_label(self, code):
        return "Pressure available" if code == 0 else "Not available"


class PyrometerHistoryLogger:
    """Independent temperature database; existing pressure storage is untouched."""
    def __init__(self):
        self.path = DATA_DIR / "pyrometer_history.sqlite3"
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=3) as con:
            con.execute("CREATE TABLE IF NOT EXISTS readings (port TEXT NOT NULL, time REAL NOT NULL, temp_c REAL NOT NULL)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_pyro_port_time ON readings(port,time)")

    def append(self, port: str, stamp: float, temp_c: float) -> None:
        if not math.isfinite(temp_c):
            return
        with self.lock, sqlite3.connect(self.path, timeout=3) as con:
            con.execute("INSERT INTO readings (port,time,temp_c) VALUES (?,?,?)", (port, stamp, temp_c))

    def history(self, port: str, seconds: Optional[int], limit: Optional[int] = 1800) -> List[Tuple[float, float]]:
        with self.lock, sqlite3.connect(self.path, timeout=3) as con:
            if seconds is None:
                data = con.execute("SELECT time,temp_c FROM readings WHERE port=? ORDER BY time", (port,)).fetchall()
            else:
                data = con.execute("SELECT time,temp_c FROM readings WHERE port=? AND time>=? ORDER BY time",
                                   (port, time.time()-seconds)).fetchall()
        if limit is not None and len(data) > limit:
            # Preserve both endpoints, with evenly spaced intermediate points.
            data = [data[round(j*(len(data)-1)/(limit-1))] for j in range(limit)]
        return data


pyrometer_logger = PyrometerHistoryLogger()


class ImpacPyrometerPanel(ttk.LabelFrame):
    """Independent IN 140/5 / IS/IGA 140 panel; serial I/O never runs in the Tk thread."""

    device_type = IMPAC_DEVICE_TYPE

    def __init__(self, parent, app: "UHVSentinelApp", name: str, port: str, baudrate: int = 19200,
                 saved_state: Optional[Dict[str, Any]] = None):
        super().__init__(parent, text=name, style="Card.TLabelframe")
        state = saved_state or {}
        self.app = app
        self.port = port
        self.name_var = tk.StringVar(value=name)
        self.device = Impac140(port, baudrate, int(state.get("address", 0)))
        # Keep last confirmed values separate from temporary search probes.
        # Other panels can trigger autosave while a COM search is running.
        self._saved_baudrate = self.device.baudrate
        self._saved_address = self.device.address
        self.stop_event = threading.Event()
        # Serial operations belong to the pyrometer worker; cancellation is
        # checked inside each UPP exchange to release COM on exit/unplug.
        self.device.cancel_event = self.stop_event
        self.commands: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.connection_var = tk.StringVar(value="Connecting...")
        self.alert_summary_var = tk.StringVar(value="Independent pyrometer monitor")
        self.model_var = tk.StringVar(value="Model: not identified")
        self.temperature_var = tk.StringVar(value="--.- °C")
        self.last_var = tk.StringVar(value="No readings yet")
        self.error_var = tk.StringVar(value="")
        self.info_var = tk.StringVar(value="Settings: waiting for communication")
        self.operation_var = tk.StringVar(value="")
        self.graph_range_var = tk.StringVar(value=state.get("graph_range", "1 h"))
        if self.graph_range_var.get() not in GRAPH_RANGE_OPTIONS:
            self.graph_range_var.set("1 h")
        self.emissivity_var = tk.StringVar(value="")
        self.t90_var = tk.StringVar(value="")
        self.clear_var = tk.StringVar(value="")
        self.analog_var = tk.StringVar(value="")
        self.unit_var = tk.StringVar(value="")
        self.laser_var = tk.StringVar(value="State unknown")
        self.wait_var = tk.StringVar(value="")
        self.low_var = tk.StringVar(value="")
        self.high_var = tk.StringVar(value="")
        self._settings: Dict[str, str] = {}
        self._full_range: Optional[Tuple[int, int]] = None
        self._graph_points: List[Tuple[float, float]] = []
        self._last_graph = 0.0
        self._last_error = ""
        self._validated = False
        self._finding = False
        self.serial_description_var = tk.StringVar()
        self._refresh_serial_description()
        self._build()
        self.thread = threading.Thread(target=self._work, name=f"IMPAC-{port}", daemon=True)
        self.thread.start()

    @property
    def display_name(self) -> str:
        return self.name_var.get().strip() or self.port

    @property
    def configured_baudrate(self) -> int:
        return self._saved_baudrate

    def state_for_config(self) -> Dict[str, Any]:
        return {"address": self._saved_address, "graph_range": self.graph_range_var.get()}

    def _refresh_serial_description(self) -> None:
        self.serial_description_var.set(
            f"{self.port}  ·  {self.device.baudrate} baud  ·  8E1  ·  address {self.device.address:02d}"
        )

    def _build(self) -> None:
        self.configure(width=PANEL_MIN_WIDTH)
        self.columnconfigure(0, weight=1)
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self.serial_description_var).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Button(header, text="Remove panel", command=self.remove).grid(row=0, column=2, sticky="e")
        ttk.Label(header, textvariable=self.connection_var).grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(header, text="Retry", command=self.retry_connection, width=7).grid(
            row=1, column=1, sticky="e", pady=(4, 0), padx=(3, 3))
        self.find_button = ttk.Button(header, text="Find connection", command=self.find_connection)
        self.find_button.grid(row=1, column=2, sticky="e", pady=(4, 0))

        info = ttk.Frame(self)
        info.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        info.columnconfigure(0, weight=1)
        ttk.Label(info, textvariable=self.model_var).grid(row=0, column=0, sticky="w")
        ttk.Label(info, textvariable=self.temperature_var, font=FONT_H1).grid(row=1, column=0, sticky="w", pady=3)
        ttk.Label(info, textvariable=self.last_var).grid(row=2, column=0, sticky="w")
        ttk.Label(info, textvariable=self.error_var, wraplength=470, foreground=UNIBAS_RED).grid(row=3, column=0, sticky="w")

        graph = ttk.LabelFrame(self, text="Temperature history (°C)")
        graph.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        graph.columnconfigure(3, weight=1)
        ttk.Label(graph, text="Range").grid(row=0, column=0, padx=4, pady=4)
        self.range_combo = ttk.Combobox(graph, textvariable=self.graph_range_var,
                                        values=GRAPH_RANGE_OPTIONS, width=8, state="readonly")
        self.range_combo.grid(row=0, column=1, padx=4, pady=4)
        self.range_combo.bind("<<ComboboxSelected>>", lambda e: self._range_changed())
        ttk.Button(graph, text="Export CSV", command=self.export_csv).grid(row=0, column=2, padx=4, pady=4)
        self.canvas = tk.Canvas(graph, width=GRAPH_CANVAS_WIDTH, height=230,
                                background=UNIBAS_WHITE, highlightbackground=UNIBAS_GRAY5,
                                highlightthickness=1)
        self.canvas.grid(row=1, column=0, columnspan=4, sticky="ew", padx=4, pady=4)
        self.canvas.bind("<Configure>", lambda event: self._draw_graph())
        self.canvas.bind("<Motion>", self._hover_graph)
        self.canvas.bind("<Leave>", lambda event: self.canvas.delete("hover"))

        settings = ttk.LabelFrame(self, text="Pyrometer settings — changes sent to instrument")
        settings.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        for c in (1, 3):
            settings.columnconfigure(c, weight=1)

        def setting(row, label, var, col, choices=None, width=13):
            ttk.Label(settings, text=label).grid(row=row, column=col, sticky="w", padx=(8, 3), pady=4)
            if choices:
                widget = ttk.Combobox(settings, textvariable=var, values=choices, state="readonly", width=width)
            else:
                widget = ttk.Entry(settings, textvariable=var, width=width)
            widget.grid(row=row, column=col+1, sticky="ew", padx=3, pady=4)
            return widget

        setting(0, "Emissivity [%]", self.emissivity_var, 0)
        ttk.Button(settings, text="Set ε", command=self.change_emissivity).grid(row=0, column=4, padx=4)
        setting(1, "Response t90", self.t90_var, 0, list(T90.values()), 20)
        ttk.Button(settings, text="Apply", command=lambda: self.change_choice("ez", self.t90_var, T90)).grid(row=1, column=4, padx=4)
        setting(2, "Peak storage", self.clear_var, 0, list(CLEAR.values()), 20)
        ttk.Button(settings, text="Apply", command=lambda: self.change_choice("lz", self.clear_var, CLEAR)).grid(row=2, column=4, padx=4)
        setting(3, "Analog output", self.analog_var, 0, ["0–20 mA", "4–20 mA"], 20)
        ttk.Button(settings, text="Apply", command=self.change_analog).grid(row=3, column=4, padx=4)
        setting(4, "Display units", self.unit_var, 0, ["°C", "°F"])
        ttk.Button(settings, text="Apply", command=self.change_unit).grid(row=4, column=4, padx=4)
        setting(5, "RS485 delay [bit]", self.wait_var, 0)
        ttk.Button(settings, text="Apply", command=self.change_delay).grid(row=5, column=4, padx=4)
        setting(6, "Range from [°C]", self.low_var, 0)
        setting(7, "Range to [°C]", self.high_var, 0)
        ttk.Button(settings, text="Set range", command=self.change_subrange).grid(row=7, column=4, padx=4)

        bottom = ttk.Frame(self)
        bottom.grid(row=4, column=0, sticky="ew", padx=8, pady=(3, 4))
        ttk.Button(bottom, text="Read all settings", command=lambda: self._queue("settings", None)).pack(side="left", padx=(0, 6))
        ttk.Button(bottom, text="Clear stored peak", command=self.clear_peak).pack(side="left", padx=(0, 6))
        ttk.Button(bottom, text="Laser ON", command=self.toggle_laser).pack(side="left", padx=(0, 4))
        ttk.Button(bottom, text="Laser OFF", command=self.laser_off).pack(side="left")
        ttk.Label(bottom, textvariable=self.laser_var).pack(side="left", padx=6)
        ttk.Label(self, textvariable=self.info_var, justify="left", wraplength=490).grid(
            row=5, column=0, sticky="ew", padx=12, pady=(2, 3))
        ttk.Label(self, textvariable=self.operation_var, wraplength=490).grid(
            row=6, column=0, sticky="ew", padx=12, pady=(0, 8))
        ttk.Button(self, text="Copy connection diagnostics", command=self.copy_connection_diagnostics).grid(
            row=7, column=0, sticky="w", padx=10, pady=(0, 7))

    def _discard_pending_writes(self) -> None:
        """Do not replay old setting changes after the COM device reconnects."""
        waiting = []
        try:
            while True:
                action, payload = self.commands.get_nowait()
                if action in ("retry", "discover"):
                    waiting.append((action, payload))
        except queue.Empty:
            pass
        for command in waiting:
            self.commands.put(command)

    def retry_connection(self) -> None:
        if self._finding:
            return
        self.connection_var.set("Retrying connection...")
        self.error_var.set("")
        self.commands.put(("retry", None))

    def find_connection(self) -> None:
        if self._finding:
            return
        if not messagebox.askyesno(
            "Find IMPAC connection (read-only)",
            "Search this COM port for an IN 140/5 (or IS/IGA 140) using the documented baud "
            "rates and addresses?\n\nThis sends IDENTIFICATION QUERIES ONLY (no setting changes). "
            "It may use UPP address 99, which asks every connected instrument "
            "to reply. Use this only if there is ONE pyrometer on this cable. "
            "Do NOT search a shared RS485 bus with multiple pyrometers.\n\nContinue?",
        ):
            return
        self._finding = True
        self.find_button.configure(state="disabled")
        self.connection_var.set("Searching...")
        self.error_var.set("")
        self.operation_var.set("Read-only serial search started. It may take around 10–20 seconds.")
        self.commands.put(("discover", None))

    def on_probe_status(self, message: str) -> None:
        self.connection_var.set(message)

    def on_probe_finished(self, success: bool) -> None:
        self._finding = False
        self.find_button.configure(state="normal")
        self._refresh_serial_description()
        if success:
            self.connection_var.set("Connected")
            self.app.request_save_config()
        else:
            self.connection_var.set("Not detected — check cable / switch")

    def copy_connection_diagnostics(self) -> None:
        from serial.tools import list_ports
        ports = list_ports.comports()
        info = (
            f"IMPAC connection diagnostics\n"
            f"Port: {self.port}\nCurrent speed: {self.device.baudrate} baud, 8E1\n"
            f"UPP address: {self.device.address:02d}\n"
            f"Model: {self.device.identity or 'not identified'}\n"
            f"Last command: {self.device.last_tx!r}\n"
            f"Last received bytes: {self.device.last_rx!r}\n"
            f"Last error: {self._last_error or self.error_var.get() or '(none)'}\n\n"
            "Windows COM ports:\n" +
            "\n".join(f"{p.device}: {p.description}" for p in ports) +
            "\n\nProbe attempts:\n" +
            "\n".join(self.device.probe_log or ["No search has been run yet."])
        )
        self.clipboard_clear()
        self.clipboard_append(info)
        self.operation_var.set("Connection diagnostics copied — paste them into ChatGPT if needed.")

    def _queue(self, action: str, payload: object) -> None:
        if not self.device.verified:
            messagebox.showwarning("Pyrometer not identified", "Wait until an IN 140/5, IS 140 or IGA 140 is identified before changing settings.")
            return
        self.commands.put((action, payload))

    def change_emissivity(self) -> None:
        try:
            value = float(self.emissivity_var.get().replace(",", "."))
            if not math.isfinite(value) or not 10 <= value <= 120:
                raise ValueError("Enter a percentage between 10 and 120")
        except ValueError as exc:
            messagebox.showerror("Invalid emissivity", str(exc))
            return
        self._queue("write", ("em", value))

    def change_choice(self, mnemonic: str, var: tk.StringVar, choices: dict) -> None:
        try:
            code = next(key for key, label in choices.items() if label == var.get())
        except StopIteration:
            messagebox.showwarning("Select an option", "Choose a supported value from the list")
            return
        self._queue("write", (mnemonic, code))

    def change_analog(self) -> None:
        if self.analog_var.get() not in ("0–20 mA", "4–20 mA"):
            messagebox.showwarning("Invalid output", "Select an analog output range first")
            return
        self._queue("write", ("as", 0 if self.analog_var.get() == "0–20 mA" else 1))

    def change_unit(self) -> None:
        if self.unit_var.get() not in ("°C", "°F"):
            messagebox.showwarning("Invalid unit", "Select °C or °F first")
            return
        self._queue("write", ("fh", 0 if self.unit_var.get() == "°C" else 1))

    def change_delay(self) -> None:
        try:
            val = int(self.wait_var.get())
            if val not in range(100):
                raise ValueError("Must be 0..99")
        except ValueError as exc:
            messagebox.showerror("Invalid delay", str(exc))
            return
        self._queue("write", ("tw", val))

    def change_subrange(self) -> None:
        if self._full_range is None:
            messagebox.showerror("Unknown range", "Read settings to obtain the full instrument temperature range")
            return
        try:
            lo, hi = int(self.low_var.get()), int(self.high_var.get())
            if not self._full_range[0] <= lo < hi <= self._full_range[1]:
                raise ValueError(f"Limits must lie within {self._full_range[0]}–{self._full_range[1]} °C")
            if self.device.identity.startswith("IN 140/5") and hi - lo < 51:
                raise ValueError("IN 140/5 subrange must span at least 51 °C")
        except ValueError as exc:
            messagebox.showerror("Invalid range", str(exc))
            return
        if messagebox.askyesno("Change instrument subrange", f"Set physical instrument subrange to {lo}–{hi} °C?\n\nThis also changes analog output scaling."):
            self._queue("write", ("m1", (lo, hi, self._full_range)))

    def clear_peak(self) -> None:
        if messagebox.askyesno("Clear stored peak", "Reset the instrument's maximum-value storage now?"):
            self._queue("write", ("lx", None))

    def toggle_laser(self) -> None:
        # Laser readback is not documented; GUI intentionally does not claim to know its state.
        if messagebox.askyesno("Laser safety", "Toggle laser targeting ON?\nDo not look into the beam or reflected beam.\n\nYes = ON, No = cancel."):
            self._queue("write", ("la", 1))
            self.laser_var.set("ON requested")

    def laser_off(self) -> None:
        self._queue("write", ("la", 0))
        self.laser_var.set("OFF requested")

    def _work(self) -> None:
        """Own COM3 exclusively; prioritize temperature over optional settings.

        Settings are read incrementally, not as a long blocking scan. Optional
        command timeouts never disconnect the pyrometer. Two consecutive
        failed *temperature* reads (or a real serial error) trigger recovery.
        """
        last_temperature = 0.0
        last_settings_refresh = 0.0
        pending_settings: list[str] = []
        missed_temperatures = 0
        try:
            while not self.stop_event.is_set():
                try:
                    action, payload = self.commands.get_nowait()
                except queue.Empty:
                    action, payload = "", None

                if action == "retry":
                    self._discard_pending_writes()
                    self.device.close()
                    pending_settings.clear()
                    self.app.events.put(("pyro_probe_status", self, "Retrying current settings..."))
                    last_temperature = 0.0
                    missed_temperatures = 0
                    continue

                if action == "discover":
                    self._discard_pending_writes()
                    pending_settings.clear()
                    try:
                        self.device.close()
                        self.app.events.put(("pyro_probe_status", self, "Searching serial settings..."))
                        name = self.device.discover(
                            progress=lambda msg: self.app.events.put(("pyro_probe_status", self, msg)),
                            stop_event=self.stop_event,
                        )
                        self.app.events.put(("pyro_identity", self, name))
                        self.app.events.put(("pyro_probe_finished", self, True))
                        pending_settings = list(OPTIONAL_SETTINGS)
                        last_temperature = 0.0
                        last_settings_refresh = time.monotonic()
                        missed_temperatures = 0
                    except InterruptedError:
                        break
                    except Exception as exc:
                        self.app.events.put(("error", self, str(exc)))
                        self.app.events.put(("pyro_probe_finished", self, False))
                    continue

                if not self.device.verified:
                    # All reconnection attempts remain in this worker thread.
                    try:
                        identity = self.device.open()
                        self.app.events.put(("pyro_identity", self, identity))
                        pending_settings = list(OPTIONAL_SETTINGS)
                        last_settings_refresh = time.monotonic()
                        last_temperature = 0.0
                        missed_temperatures = 0
                    except InterruptedError:
                        break
                    except Exception as exc:
                        self.app.events.put(("error", self, str(exc)))
                        if self.stop_event.wait(2.0):
                            break
                        continue

                try:
                    # Establish instrument units before the first measurement
                    # to prevent mixing Fahrenheit and Celsius in the graph.
                    if last_temperature == 0.0:
                        unit = self.device.read_settings(("fh",))
                        self.app.events.put(("pyro_settings", self, unit))
                        if "fh" in pending_settings:
                            pending_settings.remove("fh")

                    now = time.monotonic()
                    if last_temperature == 0.0 or now - last_temperature >= 0.90:
                        try:
                            value, warning = self.device.read_temperature()
                        except TimeoutError:
                            missed_temperatures += 1
                            # A single missed sample may be caused by converter
                            # turnaround or transient interference. Keep COM open
                            # and retry; only repeated misses mean lost contact.
                            if missed_temperatures >= 2:
                                raise
                            if self.stop_event.wait(0.20):
                                break
                            continue
                        self.app.events.put(("status", self, (time.time(), value, warning)))
                        last_temperature = time.monotonic()
                        missed_temperatures = 0

                    if action == "settings":
                        # Explicit refresh allows previously silent commands to
                        # be tried again, but still only one query per tick.
                        self.device.unavailable_settings.clear()
                        pending_settings = list(OPTIONAL_SETTINGS)
                        last_settings_refresh = time.monotonic()
                        self.app.events.put(("pyro_message", self, "Reading settings in background; temperature monitoring continues."))
                    elif action == "write":
                        command, value = payload
                        try:
                            if command == "em":
                                self.device.set_emissivity(value)
                            elif command == "tw":
                                self.device.set_wait_time(value)
                            elif command == "m1":
                                self.device.set_subrange(*value)
                            elif command == "lx":
                                self.device.clear_peak()
                            else:
                                self.device.set_choice(command, value)
                        except (TimeoutError, RuntimeError, ValueError) as exc:
                            # A failed settings write is NOT a lost temperature
                            # connection. Report it without disconnecting.
                            self.app.events.put(("pyro_message", self, f"Setting {command} failed: {exc}"))
                        else:
                            if command in OPTIONAL_SETTINGS:
                                self.device.unavailable_settings.pop(command, None)
                                if command in pending_settings:
                                    pending_settings.remove(command)
                                pending_settings.insert(0, command)
                            self.app.events.put(("pyro_message", self, f"{command} setting accepted by instrument"))

                    # Refresh configuration occasionally, without ever delaying
                    # temperature by running a 15-command blocking batch.
                    if not pending_settings and time.monotonic() - last_settings_refresh >= 60:
                        pending_settings = list(OPTIONAL_SETTINGS)
                        last_settings_refresh = time.monotonic()
                    if pending_settings and not self.stop_event.is_set():
                        setting = pending_settings.pop(0)
                        if setting not in self.device.unavailable_settings:
                            settings = self.device.read_settings((setting,))
                            self.app.events.put(("pyro_settings", self, settings))

                    self.stop_event.wait(0.10)
                except InterruptedError:
                    break
                except (TimeoutError, ConnectionError, serial.SerialException, OSError) as exc:
                    # Real temperature loss or COM error: close the handle,
                    # discard stale writes and reconnect when hardware returns.
                    self.app.events.put(("error", self, str(exc)))
                    self._discard_pending_writes()
                    pending_settings.clear()
                    self.device.close()
                    last_temperature = 0.0
                    missed_temperatures = 0
                    self.stop_event.wait(2.0)
                except Exception as exc:
                    self.app.events.put(("error", self, str(exc)))
                    self.stop_event.wait(0.7)
        finally:
            # Always release the serial port before the GUI goes away.
            self.device.close()

    def on_identity(self, name: str) -> None:
        self._validated = True
        # Re-check units after USB reconnection: never assume that the device
        # still uses the previous Celsius/Fahrenheit setting while offline.
        self._settings.pop("fh", None)
        self._saved_baudrate = self.device.baudrate
        self._saved_address = self.device.address
        self._refresh_serial_description()
        self.model_var.set(f"Verified IMPAC {name} (UPP)")
        self.connection_var.set("Connected")
        self.error_var.set("")

    def on_settings(self, settings: dict) -> None:
        self._settings.update({k: v for k, v in settings.items() if not k.startswith("_")})
        r = self._settings
        try:
            self.emissivity_var.set(f"{int(r['em']) / 10:.1f}")
        except (KeyError, ValueError):
            pass
        for key, var, choices in (("ez", self.t90_var, T90), ("lz", self.clear_var, CLEAR)):
            try:
                var.set(choices[int(r[key])])
            except (KeyError, ValueError):
                pass
        if r.get("as") in ("0", "1"):
            self.analog_var.set("4–20 mA" if r["as"] == "1" else "0–20 mA")
        if r.get("fh") in ("0", "1"):
            self.unit_var.set("°F" if r["fh"] == "1" else "°C")
        if "tw" in r:
            self.wait_var.set(r["tw"])
        try:
            self._full_range = Impac140.parse_range(r["mb"])
            lo, hi = Impac140.parse_range(r.get("me", ""))
            self.low_var.set(str(lo)); self.high_var.set(str(hi))
        except (KeyError, ValueError):
            pass
        self.info_var.set(
            f"SN: {r.get('sn', '—')}  |  FW: {r.get('vs', '—')}  |  "
            f"Case: {r.get('gt', '—')} °C  |  Max case: {r.get('tm', '—')} °C\n"
            f"Error code: {r.get('fs', '—')}  |  Interface: "
            f"{'RS485' if r.get('in') == '2' else 'RS232' if r.get('in') == '1' else '?'}  |  "
            f"Full range: {self._full_range or '?'} °C"
        )
        # Unsupported diagnostics are informational, not a COM failure.
        # An older IGA 140-L may simply ignore commands such as 'in'.
        unavailable = settings.get("_unavailable", ())
        if unavailable:
            self.info_var.set(self.info_var.get() +
                              "\nOptional readout unavailable: " + ", ".join(unavailable))
        self.app.request_save_config()

    def update_status(self, payload: tuple) -> None:
        timestamp, value, error = payload
        if error:
            self.temperature_var.set(error)
            self.last_var.set(f"{now_human(timestamp)}  ·  no valid temperature")
            return
        if value is None:
            return
        if self._settings.get("fh") not in ("0", "1"):
            self.temperature_var.set(f"{value:.1f} (units not confirmed)")
            self.error_var.set("Temperature unit could not be read; logging paused to avoid mixing °F / °C")
            return
        fahrenheit = self._settings["fh"] == "1"
        degree_unit = "°F" if fahrenheit else "°C"
        celsius = (value - 32) * 5/9 if fahrenheit else value
        self.temperature_var.set(f"{value:.1f} {degree_unit}")
        self.last_var.set(f"Updated {now_human(timestamp)}")
        self.connection_var.set("Connected")
        self.error_var.set("")  # A successful temperature read clears a transient COM error.
        try:
            pyrometer_logger.append(self.port, timestamp, celsius)
        except Exception as exc:
            self.error_var.set(f"Could not record temperature: {exc}")
        if not self._graph_points or time.monotonic() - self._last_graph > GRAPH_REFRESH_INTERVAL_S:
            self._draw_graph()

    def set_error(self, text: str) -> None:
        if not self._finding:
            self.connection_var.set("Communication error")
        self.error_var.set(text)
        self._last_error = text
        self._validated = self.device.verified

    def _range_changed(self) -> None:
        self.app.request_save_config()
        self._draw_graph()

    def _draw_graph(self) -> None:
        canvas = self.canvas
        if not canvas.winfo_exists():
            return
        self._last_graph = time.monotonic()
        w = max(canvas.winfo_width(), 350)
        h = max(canvas.winfo_height(), 210)
        canvas.delete("all")
        canvas.configure(background=UNIBAS_WHITE)
        seconds = GRAPH_RANGE_SECONDS.get(self.graph_range_var.get(), 3600)
        try:
            pts = pyrometer_logger.history(self.port, seconds)
        except Exception as exc:
            canvas.create_text(w/2, h/2, text=str(exc), fill=UNIBAS_GRAY2)
            return
        self._graph_points = []
        left, right, top, bottom = 65, w-16, 20, h-40
        canvas.create_rectangle(left, top, right, bottom, outline=UNIBAS_GRAY5)
        if not pts:
            canvas.create_text(w/2, h/2, text="Waiting for temperature data", fill=UNIBAS_GRAY2)
            return
        mint, maxt = pts[0][0], pts[-1][0]
        if mint == maxt:
            mint -= 30; maxt += 30
        values = [t for _, t in pts]
        low, high = min(values), max(values)
        pad = max(2.0, (high-low)*0.12)
        low -= pad; high += pad
        xof = lambda ts: left + (ts-mint)/(maxt-mint)*(right-left)
        yof = lambda val: bottom - (val-low)/(high-low)*(bottom-top)
        for idx in range(5):
            fraction = idx/4
            y = top + fraction*(bottom-top)
            canvas.create_line(left, y, right, y, fill=UNIBAS_GRAY6)
            canvas.create_text(left-6, y, text=f"{high-fraction*(high-low):.0f}", anchor="e", fill=UNIBAS_GRAY2, font=FONT_CAPTION)
            x = left + fraction*(right-left)
            canvas.create_line(x, top, x, bottom, fill=UNIBAS_GRAY6)
            ts = mint + fraction*(maxt-mint)
            canvas.create_text(x, bottom+8, text=time.strftime("%H:%M" if maxt-mint < 86400 else "%d.%m", time.localtime(ts)), anchor="n", fill=UNIBAS_GRAY2, font=FONT_CAPTION)
        coords = []
        for ts, val in pts:
            x, y = xof(ts), yof(val)
            coords.extend((x, y))
            self._graph_points.append((x, y, ts, val))
        if len(coords) >= 4:
            canvas.create_line(*coords, fill=GRAPH_COLORS[0], width=2)
        else:
            x, y = coords
            canvas.create_oval(x-3, y-3, x+3, y+3, fill=GRAPH_COLORS[0], outline="")

    def _hover_graph(self, event) -> None:
        self.canvas.delete("hover")
        if not self._graph_points:
            return
        closest = min(self._graph_points, key=lambda p: (p[0]-event.x)**2+(p[1]-event.y)**2)
        x, y, ts, val = closest
        if (x-event.x)**2+(y-event.y)**2 > 20**2:
            return
        self.canvas.create_text(x+8, y-10, text=f"{val:.1f} °C · {now_human(ts)}",
                                anchor="sw", fill=UNIBAS_GRAY1, font=FONT_CAPTION, tags="hover")

    def export_csv(self) -> None:
        from tkinter import filedialog
        import csv
        filename = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                                initialfile=f"IMPAC_{self.port.replace('/', '_')}_temperature.csv")
        if not filename:
            return
        try:
            history = pyrometer_logger.history(self.port, GRAPH_RANGE_SECONDS.get(self.graph_range_var.get()), limit=None)
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Local timestamp", "Unix timestamp [s]", "Temperature [°C]"])
                for ts, temperature in history:
                    writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), f"{ts:.3f}", f"{temperature:.3f}"])
            messagebox.showinfo("Export complete", f"Saved {len(history)} readings to:\n{filename}")
        except Exception as exc:
            messagebox.showerror("Export error", str(exc))

    def refresh_remote_control_visibility(self):
        pass

    def update_button_states(self):
        pass

    def _force_off_button_red(self):
        pass

    def remove(self) -> None:
        self.stop_event.set()
        # Never close a port from Tk while the worker is in a serial read.
        # The worker's finally block owns that cleanup.
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=3.0)
        if not self.thread.is_alive():
            self.device.close()
        self.app.remove_panel(self)



class UHVSentinelApp(tk.Tk):
    def __init__(self):
        super().__init__()
        apply_color_mode(load_saved_dark_mode(), load_saved_theme_file())
        setup_unibas_theme(self)
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(1080, 560)
        self.events: "queue.Queue[Tuple[str, Any, object]]" = queue.Queue()
        self.panels: List[Any] = []
        self.web_monitor = LocalPressureWebServer()
        self.device_type_var = tk.StringVar(value=NGC3_DEVICE_TYPE)
        self.pyro_address_var = tk.StringVar(value="00")
        self.epimax_address_var = tk.StringVar(value="1")
        self.epimax_units_var = tk.StringVar(value="mBar")
        self.tdk_protocol_var = tk.StringVar(value="Genesys (legacy GEN RS232)")
        self.tdk_address_var = tk.StringVar(value="6")
        self.layout_columns_var = tk.IntVar(value=2)
        self.reorder_panel_var = tk.StringVar(value="")
        self._profile_loading = False
        self.port_var = tk.StringVar()
        self.name_var = tk.StringVar(value="NGC3")
        self.baud_var = tk.IntVar(value=DEFAULT_BAUD)
        self.alert_banner_var = tk.StringVar(value="Alerts: none")
        self._loaded_controller_state: Dict[str, Dict[str, Any]] = {}
        # Preserve saved panels whose USB serial adapters are temporarily unplugged.
        self._offline_controllers: List[Dict[str, Any]] = []
        self._relayout_after: Optional[str] = None
        self._config_save_after: Optional[str] = None
        self._last_saved_config_text = ""
        self.pending_update = None
        self._build()
        self.refresh_ports()
        self.after(100, self.process_events)
        self.after(1000, self.refresh_alert_summary)
        self.bind("<Configure>", lambda _e: self.schedule_relayout())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.load_config()
        self.web_monitor.start()
        self.refresh_alert_summary()
        self.after(800, self._show_update_result)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(10, 8, 10, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(3, weight=1)
        toolbar.columnconfigure(6, weight=1)

        ttk.Label(toolbar, text="Device").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
        self.device_type_combo = ttk.Combobox(
            toolbar, textvariable=self.device_type_var, values=DEVICE_TYPE_OPTIONS, width=28, state="readonly"
        )
        self.device_type_combo.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=2)
        self.device_type_combo.bind("<<ComboboxSelected>>", self.on_device_type_changed)

        ttk.Label(toolbar, text="Port").grid(row=0, column=2, sticky="w", padx=(0, 4), pady=2)
        self.port_combo = ttk.Combobox(toolbar, textvariable=self.port_var, width=24, state="normal")
        self.port_combo.grid(row=0, column=3, sticky="ew", padx=(0, 6), pady=2)
        ttk.Button(toolbar, text="Refresh", command=self.refresh_ports, style="Ghost.TButton").grid(row=0, column=4, padx=(0, 10), pady=2)

        ttk.Label(toolbar, text="Name").grid(row=0, column=5, sticky="e", padx=(0, 4), pady=2)
        ttk.Entry(toolbar, textvariable=self.name_var, width=16).grid(row=0, column=6, sticky="ew", padx=(0, 10), pady=2)

        ttk.Label(toolbar, text="Baud").grid(row=0, column=7, sticky="e", padx=(0, 4), pady=2)
        self.baud_combo = ttk.Combobox(toolbar, textvariable=self.baud_var, values=[1200, 2400, 4800, 9600], width=8, state="readonly")
        self.baud_combo.grid(row=0, column=8, sticky="w", padx=(0, 10), pady=2)

        actions = ttk.Frame(toolbar)
        actions.grid(row=1, column=0, columnspan=9, sticky="ew", pady=(6, 0))
        ttk.Button(actions, text="Add", command=self.add_selected_controller, style="Primary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Options", command=self.open_options_window, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Profiles", command=self.open_profiles_window, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Alerts / Email", command=self.open_alerts_window, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Browser monitor", command=self.open_browser_monitor, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Help", command=self.open_readme_window, style="Ghost.TButton").pack(side="left")
        self.pyro_addr_entry = ttk.Entry(actions, textvariable=self.pyro_address_var, width=4)
        self.pyro_addr_label = ttk.Label(actions, text="IMPAC UPP address (00–97)")
        self.pyro_addr_entry.pack(side="right")
        self.pyro_addr_label.pack(side="right", padx=(6, 4))
        self.pyro_addr_entry.pack_forget()
        self.pyro_addr_label.pack_forget()
        self.epimax_addr_entry = ttk.Entry(actions, textvariable=self.epimax_address_var, width=4)
        self.epimax_addr_label = ttk.Label(actions, text="EpiMax Modbus address")
        self.epimax_units_combo = ttk.Combobox(actions, textvariable=self.epimax_units_var,
                                               values=["mBar", "Torr", "Pa"], width=6,
                                               state="readonly")
        self.epimax_units_label = ttk.Label(actions, text="Gauge units (match display)")
        self.tdk_protocol_label = ttk.Label(actions, text="TDK series")
        self.tdk_protocol_combo = ttk.Combobox(actions, textvariable=self.tdk_protocol_var,
             values=["Genesys (legacy GEN RS232)", "Z+ (SCPI RS232 / USB-serial)"], width=28, state="readonly")
        self.tdk_addr_label = ttk.Label(actions, text="Address")
        self.tdk_addr_entry = ttk.Entry(actions, textvariable=self.tdk_address_var, width=3)

        layout_bar = ttk.Frame(toolbar)
        layout_bar.grid(row=3, column=0, columnspan=9, sticky="ew", pady=(6, 0))
        ttk.Label(layout_bar, text="Panel layout:").pack(side="left", padx=(0, 5))
        ttk.Label(layout_bar, text="Columns").pack(side="left", padx=(0, 4))
        columns_combo = ttk.Combobox(layout_bar, textvariable=self.layout_columns_var,
                 values=[1,2,3], width=3, state="readonly")
        columns_combo.pack(side="left", padx=(0, 14))
        columns_combo.bind("<<ComboboxSelected>>", lambda _event: (self.relayout_panels(), self.request_save_config()))
        ttk.Label(layout_bar, text="Move:").pack(side="left", padx=(0, 4))
        self.reorder_combo = ttk.Combobox(layout_bar, textvariable=self.reorder_panel_var, width=28, state="readonly")
        self.reorder_combo.pack(side="left", padx=(0, 5))
        ttk.Button(layout_bar, text="↑", width=3, command=lambda: self.move_selected_panel(-1)).pack(side="left",padx=3)
        ttk.Button(layout_bar, text="↓", width=3, command=lambda: self.move_selected_panel(1)).pack(side="left",padx=3)

        ttk.Label(toolbar, textvariable=self.alert_banner_var, wraplength=900, justify="left", style="Banner.TLabel").grid(row=2, column=0, columnspan=9, sticky="ew", pady=(8, 0))

        self.scroller = ScrollableFrame(self)
        self.scroller.grid(row=1, column=0, sticky="nsew")

    def open_browser_monitor(self):
        urls = self.web_monitor.urls()
        if not urls:
            messagebox.showerror(
                "Browser monitor unavailable",
                self.web_monitor.start_error or "The local browser monitor could not be started.",
            )
            return

        win = tk.Toplevel(self)
        win.title("Local browser monitor")
        win.geometry("650x330")
        win.minsize(520, 260)
        win.configure(background=UNIBAS_GRAY6)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        top = ttk.Frame(win, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text="Open on this PC", command=lambda: webbrowser.open(self.web_monitor.local_url()), style="Primary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Close", command=win.destroy, style="Secondary.TButton").pack(side="right")

        text = tk.Text(
            win, wrap="word", padx=14, pady=14, background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1,
            insertbackground=UNIBAS_GRAY1, relief="flat", borderwidth=0, font=FONT_BODY,
        )
        text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        private_urls = [url for url in urls if "127.0.0.1" not in url]
        text.insert("end", "Read-only pressure dashboard with the active GUI theme and pressure graphs.\n\n")
        text.insert("end", "Important: 127.0.0.1 always means the computer on which the browser is running.\n")
        text.insert("end", "It works only when the browser is on this monitor computer.\n\n")
        text.insert("end", "On this monitor computer:\n")
        text.insert("end", self.web_monitor.local_url() + "\n\n")
        if private_urls:
            text.insert("end", "From the scanning PC on the same private local network, use one of these LAN addresses:\n")
            for url in private_urls:
                text.insert("end", url + "\n")
            text.insert("end", f"\nIf the LAN address is blocked, allow TCP port {self.web_monitor.port} in Windows Firewall only for the Private network / Local subnet.\n")
        else:
            text.insert("end", "No private LAN address was detected, so only this computer can access the page.\n")
        text.insert("end", "\nThe browser page is read-only. The program does not configure router port forwarding or public internet exposure.\n")
        text.configure(state="disabled")

    def open_manual_pdf(self):
        manual_path = project_file(MANUAL_FILENAME)
        if manual_path.exists():
            webbrowser.open(manual_path.as_uri())
            return
        if messagebox.askyesno(
            "Manual PDF not found",
            f"Could not find {MANUAL_FILENAME} next to uhv_sentinel.py.\n\nOpen the online manual instead?",
        ):
            webbrowser.open(MANUAL_URL)

    def open_manual_url(self):
        webbrowser.open(MANUAL_URL)

    def open_impac_manual_pdf(self):
        manual_path = project_file(IMPAC_MANUAL_FILENAME)
        if manual_path.is_file():
            webbrowser.open(manual_path.resolve().as_uri())
            return
        if messagebox.askyesno(
            "Pyrometer manual PDF not found",
            f"Could not find {IMPAC_MANUAL_FILENAME} next to uhv_sentinel.py.\n\nOpen the online manual instead?",
        ):
            self.open_impac_manual_url()

    def open_impac_manual_url(self):
        webbrowser.open(IMPAC_MANUAL_URL)

    def open_readme_window(self):
        readme_path = project_file(README_FILENAME)
        try:
            readme_text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else README_FALLBACK
        except Exception as exc:
            readme_text = f"Could not read {README_FILENAME}: {exc}\n\n" + README_FALLBACK

        win = tk.Toplevel(self)
        win.title("UHV Sentinel Help")
        win.geometry("850x700")
        win.minsize(600, 420)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        buttons = ttk.Frame(win, padding=(8, 8, 8, 4))
        buttons.grid(row=0, column=0, sticky="ew")
        ttk.Button(buttons, text="Open local manual PDF", command=self.open_manual_pdf, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Open online manual", command=self.open_manual_url, style="Ghost.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="IN 140/5 manual", command=self.open_impac_manual_pdf, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="IN 140/5 online", command=self.open_impac_manual_url, style="Ghost.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Close", command=win.destroy, style="Secondary.TButton").pack(side="right")

        frame = ttk.Frame(win, padding=(8, 0, 8, 8))
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text_widget = tk.Text(frame, wrap="word", padx=14, pady=14, background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, insertbackground=UNIBAS_GRAY1, relief="flat", borderwidth=0, font=FONT_BODY)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scroll.set)
        text_widget.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        render_markdown_help(text_widget, readme_text)
        text_widget.configure(state="disabled")

    def on_device_type_changed(self, _event=None):
        device_type = self.device_type_var.get()
        for widget in (self.pyro_addr_entry, self.pyro_addr_label,
                       self.epimax_addr_entry, self.epimax_addr_label,
                       self.epimax_units_combo, self.epimax_units_label,
                       self.tdk_protocol_label, self.tdk_protocol_combo,
                       self.tdk_addr_label, self.tdk_addr_entry):
            widget.pack_forget()
        if device_type == IMPAC_DEVICE_TYPE:
            self.pyro_addr_entry.pack(side="right")
            self.pyro_addr_label.pack(side="right", padx=(6, 4))
            values = IMPAC_BAUDS
            self.baud_var.set(19200)
            self.name_var.set("Pyrometer")
        elif device_type == EPIMAX_DEVICE_TYPE:
            self.epimax_addr_entry.pack(side="right")
            self.epimax_addr_label.pack(side="right", padx=(6, 4))
            self.epimax_units_combo.pack(side="right")
            self.epimax_units_label.pack(side="right", padx=(6, 4))
            values = [4800, 9600, 19200, 38400]
            self.baud_var.set(19200)
            self.name_var.set("EpiMax IG")
        elif device_type == TDK_DEVICE_TYPE:
            self.tdk_addr_entry.pack(side="right")
            self.tdk_addr_label.pack(side="right", padx=(6, 4))
            self.tdk_protocol_combo.pack(side="right")
            self.tdk_protocol_label.pack(side="right", padx=(6, 4))
            values = [1200, 2400, 4800, 9600, 19200, 38400]
            self.baud_var.set(9600)
            self.name_var.set("TDK supply")
        elif device_type == AGILENT_DEVICE_TYPE:
            values = [1200, 2400, 4800, 9600]
            self.baud_var.set(9600)  # Agilent factory default, configurable on device
            self.name_var.set("Ion Pumps")
        elif device_type == PFEIFFER_DEVICE_TYPE:
            values = [9600, 19200, 38400]
            self.name_var.set("Loadlock")
        else:
            values = [1200, 2400, 4800, 9600]
            self.name_var.set("NGC3")
        self.baud_combo["values"] = values
        if self.baud_var.get() not in values:
            self.baud_var.set(values[0])

    def refresh_ports(self):
        ports = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and (not self.port_var.get() or self.port_var.get() not in ports):
            self.port_var.set(ports[0])

    def add_selected_controller(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("No serial port", "Select a serial port first.")
            return
        if any(panel.port == port for panel in self.panels):
            messagebox.showerror("Already added", f"{port} already has a panel.")
            return
        name = self.name_var.get().strip() or port
        saved_state = dict(self._loaded_controller_state.get(port, {}))
        device_type = self.device_type_var.get()
        if device_type == IMPAC_DEVICE_TYPE:
            try:
                address = int(self.pyro_address_var.get().strip())
                if not 0 <= address <= 97:
                    raise ValueError("Address must be 00–97")
                saved_state["address"] = address
            except ValueError as exc:
                messagebox.showerror("Invalid IMPAC address", str(exc))
                return
            panel = ImpacPyrometerPanel(
                self.scroller.inner, self, name=name, port=port,
                baudrate=int(self.baud_var.get()), saved_state=saved_state,
            )
        elif device_type == EPIMAX_DEVICE_TYPE:
            try:
                address = int(self.epimax_address_var.get().strip())
                if not 1 <= address <= 99:
                    raise ValueError("EpiMax Modbus address must be 1–99")
                saved_state.update(address=address, pressure_units=self.epimax_units_var.get())
            except ValueError as exc:
                messagebox.showerror("Invalid EpiMax settings", str(exc))
                return
            panel = EpimaxPVCPanel(
                self.scroller.inner, self, name=name, port=port,
                baudrate=int(self.baud_var.get()), saved_state=saved_state,
            )
        elif device_type == TDK_DEVICE_TYPE:
            try:
                address = int(self.tdk_address_var.get())
                if not 0 <= address <= 30:
                    raise ValueError("Address must be 0–30")
                saved_state["address"] = address
                saved_state["protocol"] = self.tdk_protocol_var.get()
            except ValueError as exc:
                messagebox.showerror("Invalid TDK serial settings", str(exc))
                return
            panel = TDKPanel(self.scroller.inner, self, name=name, port=port,
                             baudrate=int(self.baud_var.get()), saved_state=saved_state)
        elif device_type == AGILENT_DEVICE_TYPE:
            panel = Agilent4UHVPanel(
                self.scroller.inner, self, name=name, port=port,
                baudrate=int(self.baud_var.get()), saved_state=saved_state,
            )
        elif device_type == PFEIFFER_DEVICE_TYPE:
            panel = PfeifferDualGaugePanel(
                self.scroller.inner, self, name=name, port=port,
                baudrate=int(self.baud_var.get()), saved_state=saved_state,
            )
        else:
            panel = DevicePanel(
                self.scroller.inner, self, name=name, port=port,
                baudrate=int(self.baud_var.get()), saved_state=saved_state,
            )
        self.panels.append(panel)
        self._offline_controllers = [item for item in self._offline_controllers if item.get("port") != port]
        self.relayout_panels()
        self.save_config()

    def move_selected_panel(self, delta: int):
        index = self.reorder_combo.current()
        if index < 0:
            return
        next_index = index + delta
        if not (0 <= next_index < len(self.panels)):
            return
        self.panels[index], self.panels[next_index] = self.panels[next_index], self.panels[index]
        self.relayout_panels()
        self.reorder_combo.current(next_index)
        self.save_config()

    def remove_panel(self, panel: DevicePanel):
        if self._profile_loading:
            return
        if panel in self.panels:
            self.panels.remove(panel)
        panel.destroy()
        self.relayout_panels()
        self.save_config()

    def schedule_relayout(self):
        if self._relayout_after is not None:
            try:
                self.after_cancel(self._relayout_after)
            except Exception:
                pass
        self._relayout_after = self.after(120, self._run_scheduled_relayout)

    def _run_scheduled_relayout(self):
        self._relayout_after = None
        self.relayout_panels()

    def relayout_panels(self):
        if not hasattr(self, "scroller"):
            return

        # Configurable panel order and number of columns; stored in profiles.
        try:
            columns = max(1, min(3, int(self.layout_columns_var.get())))
        except (ValueError, tk.TclError):
            columns = 2
        columns = min(columns, max(1, len(self.panels)))
        canvas_width = max(self.scroller.canvas.winfo_width(), 1)
        try:
            self.scroller.canvas.itemconfigure(self.scroller.window_id, width=canvas_width)
        except Exception:
            pass

        for child in self.scroller.inner.winfo_children():
            child.grid_forget()

        # Reset a reasonable number of old grid definitions, then configure the two visible columns.
        for c in range(max(2, len(self.panels) + 1)):
            self.scroller.inner.columnconfigure(c, weight=0, uniform="")
        for c in range(columns):
            self.scroller.inner.columnconfigure(c, weight=1, uniform="panels")

        for r in range(max(1, (len(self.panels) + columns - 1) // columns)):
            self.scroller.inner.rowconfigure(r, weight=0)

        for idx, panel in enumerate(self.panels):
            row, col = divmod(idx, columns)
            panel.grid(row=row, column=col, sticky="nsew", padx=8, pady=8)

        self.scroller.inner.update_idletasks()
        self.scroller.canvas.configure(scrollregion=self.scroller.canvas.bbox("all"))
        self.reorder_combo["values"] = [p.display_name + " (" + p.port + ")" for p in self.panels]
        if self.panels and self.reorder_combo.current() < 0:
            self.reorder_combo.current(0)
        if not self.panels:
            self.reorder_panel_var.set("")

    def process_events(self):
        """Process a bounded batch and collapse stale status messages per panel.

        An unbounded ``while`` loop can starve Tk when serial workers produce data
        faster than graph/database rendering can consume it. Keeping only the latest
        status for each panel prevents an old backlog from making the GUI look frozen.
        """
        items: List[Tuple[str, Any, object]] = []
        for _ in range(EVENTS_PER_TICK):
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                break

        latest_status: Dict[Any, object] = {}
        for kind, panel, payload in items:
            if kind == "status":
                latest_status[panel] = payload
                continue
            if kind == "message":
                messagebox.showinfo("UHV Sentinel", str(payload))
                continue
            if kind == "dialog_error":
                messagebox.showerror("UHV Sentinel", str(payload))
                continue
            if panel not in self.panels:
                continue
            if kind == "tdk_ident":
                panel.connection_var.set("Connected: " + str(payload))
            elif kind == "tdk_cmd":
                panel.connection_var.set(str(payload))
            elif kind == "pyro_identity":
                panel.on_identity(str(payload))
            elif kind == "pyro_probe_status":
                panel.on_probe_status(str(payload))
            elif kind == "pyro_probe_finished":
                panel.on_probe_finished(bool(payload))
            elif kind == "pyro_settings":
                panel.on_settings(payload)
            elif kind == "pyro_message":
                panel.operation_var.set(str(payload))
            elif kind == "error":
                panel.set_error(str(payload))
                panel.update_button_states()
                panel._force_off_button_red()
            elif kind == "save":
                self.request_save_config()

        for panel, payload in latest_status.items():
            if panel in self.panels:
                panel.update_status(payload)  # type: ignore[arg-type]

        self.after(20 if not self.events.empty() else 100, self.process_events)

    def refresh_alert_summary(self):
        active = alert_manager.active_alerts()
        if not active:
            self.alert_banner_var.set("Alerts: none")
            for p in self.panels:
                p.alert_summary_var.set("Alerts: none")
            self.web_monitor.update_alerts([])
            return
        critical = sum(1 for a in active if a.severity == "critical")
        warning = sum(1 for a in active if a.severity == "warning")
        top = active[0]
        self.alert_banner_var.set(f"Alerts: {critical} critical, {warning} warning. Latest: {top.severity.upper()} {top.controller_name}: {top.message} — {top.details}")
        for panel in self.panels:
            mine = [a for a in active if a.key.startswith(f"{panel.port}:")]
            if not mine:
                panel.alert_summary_var.set("Alerts: none")
            else:
                m = mine[0]
                panel.alert_summary_var.set(f"{len(mine)} active: {m.severity.upper()} {m.message} — {m.details}")
        self.web_monitor.update_alerts(active)

    def apply_theme_to_widgets(self):
        def walk(widget):
            try:
                if isinstance(widget, tk.Canvas):
                    widget.configure(background=UNIBAS_GRAY6 if widget is getattr(self.scroller, "canvas", None) else UNIBAS_WHITE)
                elif isinstance(widget, tk.Text):
                    widget.configure(background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1, insertbackground=UNIBAS_GRAY1)
                elif isinstance(widget, tk.Button):
                    # Only the ion-current buttons use tk.Button. Their exact colours
                    # are reapplied by update_button_states(); here we only keep the
                    # current background readable while the theme is switching.
                    bg = str(widget.cget("bg"))
                    widget.configure(fg=_readable_fg_for_bg(bg), activeforeground=_readable_fg_for_bg(bg), font=FONT_BUTTON)
                elif isinstance(widget, tk.Toplevel):
                    widget.configure(background=UNIBAS_GRAY6)
            except Exception:
                pass
            for child in widget.winfo_children():
                walk(child)
        walk(self)
        # Reapply safety-button colours after a global theme refresh. This prevents
        # the OFF button from falling back to the platform/default colour when no
        # serial connection exists.
        for panel in getattr(self, "panels", []):
            panel.refresh_remote_control_visibility()
            panel.update_button_states()
            panel._force_off_button_red()

    def _show_update_result(self) -> None:
        try:
            result = updater.take_update_result()
        except Exception:
            return
        if not result:
            return
        if result.get("success"):
            messagebox.showinfo("UHV Sentinel updated", str(result.get("details", "Update complete")))
        else:
            messagebox.showwarning("Update could not be completed", str(result.get("details", "Update failed")))

    def open_update_window(self, parent: tk.Toplevel) -> None:
        win = tk.Toplevel(parent)
        win.title("UHV Sentinel — Updates")
        win.geometry("540x240")
        win.resizable(False, False)
        win.configure(background=UNIBAS_GRAY6)
        container = ttk.Frame(win, padding=16)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text=f"Current version: v{__version__}", style="Card.TLabel").pack(anchor="w", pady=(0, 10))
        status = tk.StringVar(value="Use GitHub Releases to check for a newer stable version.")
        ttk.Label(container, textvariable=status, wraplength=490, justify="left", style="Card.TLabel").pack(anchor="w", pady=(0, 12))
        buttons = ttk.Frame(container)
        buttons.pack(anchor="w")
        check = ttk.Button(buttons, text="Check for updates", style="Primary.TButton")
        check.pack(side="left", padx=(0, 8))
        check.configure(command=lambda: self.check_for_updates(check, status, win))
        ttk.Button(buttons, text="Close", command=win.destroy, style="Secondary.TButton").pack(side="left")
        ttk.Label(container, text="Updates stop pressure monitoring during installation. "
                  "Recorded data and settings are not removed.", style="Caption.TLabel",
                  wraplength=490, justify="left").pack(anchor="w", pady=(16, 0))
        win.transient(parent)

    def check_for_updates(self, button: ttk.Button, status_var: tk.StringVar, owner: tk.Toplevel) -> None:
        """Keep all Tk calls on the GUI thread; network work runs in a worker."""
        if not owner.winfo_exists():
            return
        button.configure(state="disabled")
        status_var.set("Contacting GitHub Releases…")
        results: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        state: dict = {"release": None, "mode": "check"}

        def check_worker():
            try:
                release = updater.latest_release()
                results.put(("release", release))
            except Exception as exc:
                results.put(("error", str(exc)))

        def download_worker(release):
            try:
                prepared = updater.prepare_release(release)
                results.put(("download", prepared))
            except Exception as exc:
                results.put(("error", str(exc)))

        def install_selected():
            release = state["release"]
            if not release:
                return
            installed = (Path(__file__).resolve().parent == (install_dir() / "app").resolve()
                         and (install_dir() / "install.json").is_file())
            if not installed:
                messagebox.showwarning("Installation required",
                    "You are running UHV Sentinel from source. Run the platform INSTALL script "
                    "first to enable safe in-app updates.", parent=owner)
                return
            if not messagebox.askyesno("Install verified update?",
                    f"Install UHV Sentinel v{release.version}?\n\n"
                    "Monitoring will stop while the application restarts. "
                    "Make sure this is safe for your experiment.\n\n"
                    "Settings and recordings are preserved.", parent=owner):
                return
            state["mode"] = "download"
            button.configure(state="disabled")
            status_var.set(f"Downloading v{release.version} and verifying SHA-256…")
            threading.Thread(target=download_worker, args=(release,), daemon=True, name="uhv-update-download").start()

        def poll():
            if not owner.winfo_exists():
                return
            try:
                kind, result = results.get_nowait()
            except queue.Empty:
                owner.after(180, poll)
                return
            if kind == "error":
                status_var.set(str(result))
                button.configure(text="Check for updates", state="normal", command=lambda: self.check_for_updates(button, status_var, owner))
            elif kind == "release":
                release = result
                if updater.newer_release(release):
                    state["release"] = release
                    status_var.set(f"v{release.version} available (installed: v{__version__}).\n"
                                   "Update installation stops monitoring briefly.")
                    button.configure(text=f"Install v{release.version}", state="normal", command=install_selected)
                else:
                    status_var.set(f"You already have the latest published version (v{__version__}).")
                    button.configure(text="Check again", state="normal", command=lambda: self.check_for_updates(button, status_var, owner))
            elif kind == "download":
                self.pending_update = result
                status_var.set("Verified update downloaded. Closing monitoring and installing…")
                self.after(200, self.on_close)
                return

        threading.Thread(target=check_worker, daemon=True, name="uhv-update-check").start()
        owner.after(180, poll)

    def open_options_window(self):
        settings = alert_manager.settings
        win = tk.Toplevel(self)
        win.title("UHV Sentinel Options")
        win.geometry("690x665")
        win.minsize(600, 570)
        win.configure(background=UNIBAS_GRAY6)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        top = ttk.Frame(win, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        save_button = ttk.Button(top, text="Saved", command=lambda: save_from_form(), style="Primary.TButton", state=tk.DISABLED)
        save_button.pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Data folder", command=lambda: open_data_folder(), style="Ghost.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Profiles", command=self.open_profiles_window, style="Ghost.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text=f"Update · v{__version__}", command=lambda: self.open_update_window(win), style="Primary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text="IN 140/5 manual", command=self.open_impac_manual_pdf, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Close", command=win.destroy, style="Secondary.TButton").pack(side="right")

        body = ttk.Frame(win, padding=10)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        form = ttk.LabelFrame(body, text="Options", style="Card.TLabelframe")
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        vars: Dict[str, Any] = {}

        def add_check(row, label, key):
            vars[key] = tk.BooleanVar(value=bool(getattr(settings, key)))
            ttk.Checkbutton(form, text=label, variable=vars[key]).grid(row=row, column=0, columnspan=2, sticky="w", padx=10, pady=5)

        def add_entry(row, label, key):
            ttk.Label(form, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=10, pady=5)
            vars[key] = tk.StringVar(value=str(getattr(settings, key, "")))
            ttk.Entry(form, textvariable=vars[key]).grid(row=row, column=1, sticky="ew", padx=10, pady=5)

        def add_combo(row, label, key, values):
            ttk.Label(form, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=10, pady=5)
            vars[key] = tk.StringVar(value=str(getattr(settings, key, values[0])))
            ttk.Combobox(form, textvariable=vars[key], values=values, width=10, state="readonly").grid(row=row, column=1, sticky="w", padx=10, pady=5)

        r = 0
        theme_files = list_theme_files()
        theme_choices = ["Built-in"] + [path.name for path in theme_files]
        current_theme = settings.theme_file if settings.theme_file in theme_choices else (ACTIVE_THEME_FILE if ACTIVE_THEME_FILE in theme_choices else "Built-in")
        ttk.Label(form, text="Theme file", style="Card.TLabel").grid(row=r, column=0, sticky="w", padx=10, pady=5)
        vars["theme_file"] = tk.StringVar(value=current_theme)
        ttk.Combobox(form, textvariable=vars["theme_file"], values=theme_choices, width=28, state="readonly").grid(row=r, column=1, sticky="w", padx=10, pady=5)
        r += 1
        add_check(r, "Dark mode", "dark_mode"); r += 1
        add_check(r, "Pressure logging", "pressure_logging_enabled"); r += 1
        add_entry(r, "Log interval [s]", "pressure_log_interval_seconds"); r += 1
        add_combo(r, "Graph range", "graph_default_range", GRAPH_RANGE_OPTIONS); r += 1
        add_check(r, "Plot selected IG only", "graph_selected_ig_only"); r += 1

        ttk.Label(form, text="Plot curves", style="Card.TLabel").grid(row=r, column=0, sticky="nw", padx=10, pady=5)
        curve_box = ttk.Frame(form, style="Card.TFrame")
        curve_box.grid(row=r, column=1, sticky="ew", padx=10, pady=5)
        for idx, gauge_name in enumerate(GRAPH_GAUGE_KEYS):
            key = f"graph_visible_{idx}"
            vars[key] = tk.BooleanVar(value=gauge_name in settings.graph_visible_gauges)
            ttk.Checkbutton(curve_box, text=gauge_name, variable=vars[key]).grid(row=idx // 2, column=idx % 2, sticky="w", padx=(0, 12), pady=1)
        r += 1

        add_check(r, "Remote controls", "remote_control_enabled"); r += 1
        add_entry(r, "IG sensitivity [mBar^-1]", "ig_sensitivity_mbar_inv"); r += 1

        ttk.Label(body, text=f"Data: {pressure_logger.data_location_text()}", style="Caption.TLabel", justify="left", wraplength=580).grid(row=1, column=0, sticky="ew", pady=(10, 0))

        def form_to_settings() -> AlertSettings:
            data = settings.to_dict()
            visible_gauges = []
            for k, v in vars.items():
                if k.startswith("graph_visible_"):
                    try:
                        idx = int(k.rsplit("_", 1)[1])
                        if v.get():
                            visible_gauges.append(GRAPH_GAUGE_KEYS[idx])
                    except Exception:
                        pass
                    continue
                if isinstance(v, tk.BooleanVar):
                    data[k] = v.get()
                elif isinstance(v, tk.StringVar):
                    value = v.get()
                    data[k] = "" if k == "theme_file" and value == "Built-in" else value
            data["graph_visible_gauges"] = visible_gauges or list(GRAPH_GAUGE_KEYS)
            return AlertSettings.from_dict(data)

        saved_signature = {"value": ""}

        def _settings_signature(obj: AlertSettings) -> str:
            return json.dumps(obj.to_dict(), sort_keys=True, default=str)

        def _update_save_button(*_args):
            try:
                changed = _settings_signature(form_to_settings()) != saved_signature["value"]
            except Exception:
                changed = True
            save_button.configure(state=(tk.NORMAL if changed else tk.DISABLED), text=("Save" if changed else "Saved"))

        def save_from_form():
            new_settings = form_to_settings()
            alert_manager.configure(new_settings)
            apply_color_mode(new_settings.dark_mode, new_settings.theme_file)
            setup_unibas_theme(self)
            self.configure(background=UNIBAS_GRAY6)
            self.apply_theme_to_widgets()
            self.save_config()
            self.refresh_alert_summary()
            for panel in self.panels:
                panel.refresh_remote_control_visibility()
                panel.update_button_states()
                if hasattr(panel, "draw_pressure_graph"):
                    panel.draw_pressure_graph()
                elif hasattr(panel, "_draw_graph"):
                    panel._draw_graph(force=True)
            saved_signature["value"] = _settings_signature(new_settings)
            _update_save_button()

        def open_data_folder():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                if platform.system() == "Darwin":
                    subprocess.run(["open", str(DATA_DIR)], check=False)
                elif platform.system() == "Windows":
                    os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
                else:
                    subprocess.run(["xdg-open", str(DATA_DIR)], check=False)
            except Exception as exc:
                messagebox.showerror("Open folder failed", str(exc))

        saved_signature["value"] = _settings_signature(form_to_settings())
        for _var in vars.values():
            if isinstance(_var, (tk.BooleanVar, tk.StringVar)):
                _var.trace_add("write", _update_save_button)
        _update_save_button()

    def open_alerts_window(self):
        settings = alert_manager.settings
        win = tk.Toplevel(self)
        win.title("UHV Sentinel Alerts / Email")
        win.geometry("920x650")
        win.minsize(700, 500)
        win.configure(background=UNIBAS_GRAY6)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        top = ttk.Frame(win, padding=12)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text="Save", command=lambda: save_from_form(), style="Primary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Send test email", command=lambda: send_test(), style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Clear alerts", command=lambda: clear_alerts(), style="Ghost.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Close", command=win.destroy, style="Secondary.TButton").pack(side="right")

        body = ttk.Frame(win, padding=12)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        form = ttk.LabelFrame(body, text="Alerts / Email", style="Card.TLabelframe")
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        form.columnconfigure(1, weight=1)

        vars: Dict[str, Any] = {}

        def add_check(row, label, key):
            vars[key] = tk.BooleanVar(value=bool(getattr(settings, key)))
            ttk.Checkbutton(form, text=label, variable=vars[key]).grid(row=row, column=0, columnspan=2, sticky="w", padx=10, pady=3)

        def add_entry(row, label, key, show=None):
            ttk.Label(form, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=10, pady=3)
            vars[key] = tk.StringVar(value=str(getattr(settings, key, "")))
            ttk.Entry(form, textvariable=vars[key], show=show).grid(row=row, column=1, sticky="ew", padx=10, pady=3)

        def add_text(row, label, key):
            ttk.Label(form, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="nw", padx=10, pady=3)
            txt = tk.Text(form, width=35, height=3, background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1,
                          insertbackground=UNIBAS_GRAY1, relief="flat", borderwidth=1, font=FONT_BODY)
            txt.insert("1.0", ", ".join(settings.recipients))
            txt.grid(row=row, column=1, sticky="ew", padx=10, pady=3)
            vars[key] = txt

        r = 0
        add_check(r, "Enable alerts", "alerts_enabled"); r += 1
        add_check(r, "Enable email alerts (critical alerts only)", "email_enabled"); r += 1
        add_text(r, "Recipients", "recipients"); r += 1
        add_entry(r, "SMTP host", "smtp_host"); r += 1
        add_entry(r, "SMTP port", "smtp_port"); r += 1
        add_check(r, "Use STARTTLS/TLS", "smtp_use_tls"); r += 1
        add_check(r, "Use SSL", "smtp_use_ssl"); r += 1
        add_entry(r, "SMTP username", "smtp_username"); r += 1
        add_entry(r, "Sender / From", "smtp_sender"); r += 1
        ttk.Label(form, text="SMTP password\n(runtime only)", style="Card.TLabel").grid(row=r, column=0, sticky="w", padx=10, pady=3)
        vars["smtp_password"] = tk.StringVar(value="")
        ttk.Entry(form, textvariable=vars["smtp_password"], show="*").grid(row=r, column=1, sticky="ew", padx=10, pady=3); r += 1
        add_entry(r, "Cooldown per alert [s]", "cooldown_seconds"); r += 1
        add_entry(r, "No-response delay [s]", "no_response_seconds"); r += 1
        add_entry(r, "IG pressure critical [mBar]", "ig_pressure_critical_mbar"); r += 1
        add_entry(r, "Pirani warning [mBar]\n(blank = disabled)", "pirani_pressure_warning_mbar"); r += 1
        add_entry(r, "Active warning [mBar]\n(blank = disabled)", "active_pressure_warning_mbar"); r += 1
        add_check(r, "Pressure-rise warning", "pressure_rise_enabled"); r += 1
        add_entry(r, "Pressure-rise factor", "pressure_rise_factor"); r += 1
        add_entry(r, "Pressure-rise window [s]", "pressure_rise_window_seconds"); r += 1

        alerts_box = ttk.LabelFrame(body, text="Active alerts / history", style="Card.TLabelframe")
        alerts_box.grid(row=0, column=1, sticky="nsew")
        alerts_box.columnconfigure(0, weight=1)
        alerts_box.rowconfigure(0, weight=1)
        text = tk.Text(alerts_box, wrap="word", width=70, background=UNIBAS_WHITE, foreground=UNIBAS_GRAY1,
                       insertbackground=UNIBAS_GRAY1, relief="flat", borderwidth=0, font=FONT_BODY)
        scroll = ttk.Scrollbar(alerts_box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scroll.grid(row=0, column=1, sticky="ns", pady=8)

        def render_alerts():
            if not win.winfo_exists():
                return
            text.configure(state="normal")
            text.delete("1.0", "end")
            active = alert_manager.active_alerts()
            history = alert_manager.recent_history()
            text.insert("end", "ACTIVE ALERTS\n", ("h",))
            if not active:
                text.insert("end", "None\n")
            for a in active:
                text.insert("end", f"[{a.severity.upper()}] {a.controller_name}: {a.message}\n{a.details}\nLast: {now_human(a.last_seen)}   Count: {a.count}\n\n")
            text.insert("end", "\nRECENT CLEARED HISTORY\n", ("h",))
            if not history:
                text.insert("end", "None\n")
            for a in history[:50]:
                text.insert("end", f"[{a.severity.upper()}] {a.controller_name}: {a.message}\n{a.details}\nLast: {now_human(a.last_seen)}\n\n")
            text.tag_configure("h", font=FONT_H5, foreground=UNIBAS_RED)
            text.configure(state="disabled")
            if win.winfo_exists():
                win.after(1500, render_alerts)
        render_alerts()

        def form_to_settings() -> AlertSettings:
            data = settings.to_dict()
            for k, v in vars.items():
                if k == "smtp_password":
                    continue
                if k == "recipients":
                    data[k] = normalize_recipients(v.get("1.0", "end"))
                elif isinstance(v, tk.BooleanVar):
                    data[k] = v.get()
                elif isinstance(v, tk.StringVar):
                    data[k] = v.get()
            return AlertSettings.from_dict(data)

        def save_from_form():
            new_settings = form_to_settings()
            set_runtime_smtp_password(vars["smtp_password"].get())
            alert_manager.configure(new_settings)
            self.save_config()
            self.refresh_alert_summary()
            for panel in self.panels:
                panel.update_button_states()

        def send_test():
            save_from_form()
            def worker():
                try:
                    alert_manager.send_test_email()
                    self.events.put(("message", self.panels[0] if self.panels else None, "Test email sent"))
                except Exception as exc:
                    self.events.put(("dialog_error", self.panels[0] if self.panels else None, f"Test email failed: {exc}"))
            threading.Thread(target=worker, daemon=True).start()

        def clear_alerts():
            alert_manager.clear()
            self.refresh_alert_summary()
            render_alerts()


    def _config_data(self) -> Dict[str, Any]:
        return {
            "controllers": [
                {
                    "name": p.display_name,
                    "port": p.port,
                    "baudrate": getattr(p, "configured_baudrate", p.device.baudrate),
                    "device_type": getattr(p, "device_type", NGC3_DEVICE_TYPE),
                    "state": p.state_for_config(),
                }
                for p in self.panels
            ] + [item for item in self._offline_controllers
                 if not any(p.port == item.get("port") for p in self.panels)],
            "alert_settings": alert_manager.settings.to_dict(),
            "layout": {
                "columns": self.layout_columns_var.get(),
                "window_geometry": self.geometry(),
                "scroll_fraction": self.scroller.canvas.yview()[0],
            },
        }

    def request_save_config(self) -> None:
        if self._profile_loading:
            return
        if self._config_save_after is None:
            self._config_save_after = self.after(CONFIG_SAVE_DEBOUNCE_MS, self._run_debounced_config_save)

    def _run_debounced_config_save(self) -> None:
        self._config_save_after = None
        self.save_config()

    def save_config(self):
        if self._profile_loading:
            return
        data = self._config_data()
        text = json.dumps(data, indent=2, sort_keys=True)
        if text == self._last_saved_config_text:
            return
        tmp_path = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(text, encoding="utf-8")
            tmp_path.replace(CONFIG_PATH)
            self._last_saved_config_text = text
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _stop_panels_for_profile(self):
        for panel in self.panels:
            panel.stop_event.set()
        for panel in list(self.panels):
            worker = getattr(panel, "thread", None)
            if worker is not None and worker.is_alive():
                worker.join(timeout=3.5)
            try:
                panel.device.close()
            except Exception:
                pass
            self.web_monitor.remove_panel(panel)
            try:
                panel.destroy()
            except tk.TclError:
                pass
        self.panels.clear()
        self._offline_controllers.clear()
        self._loaded_controller_state.clear()

    def _load_config_data(self, data: Dict[str, Any], *, replace_existing: bool = False):
        if replace_existing:
            self._stop_panels_for_profile()
        self._profile_loading = True
        try:
            if isinstance(data.get("alert_settings"), dict):
                alert_manager.configure(AlertSettings.from_dict(data["alert_settings"]))
                apply_color_mode(alert_manager.settings.dark_mode, alert_manager.settings.theme_file)
                setup_unibas_theme(self)
                self.apply_theme_to_widgets()
            layout = data.get("layout", {})
            if isinstance(layout, dict):
                try:
                    self.layout_columns_var.set(max(1, min(3, int(layout.get("columns", 2)))))
                except (ValueError, TypeError):
                    self.layout_columns_var.set(2)
                geometry = str(layout.get("window_geometry", ""))
                # Geometry can be imported from another screen; bound dimensions and
                # don't import its monitor X/Y coordinates.
                match = re.fullmatch(r"(\d{3,5})x(\d{3,5})(?:[+-]\d+[+-]\d+)?", geometry)
                if match:
                    width = max(1080, min(2500, int(match.group(1))))
                    height = max(560, min(1600, int(match.group(2))))
                    self.geometry(f"{width}x{height}")
            available = set(self.port_combo["values"])
            for item in data.get("controllers", []):
                if not isinstance(item, dict):
                    continue
                port = str(item.get("port", "")).strip()
                if not port:
                    continue
                self._loaded_controller_state[port] = item.get("state", {}) or {}
                if port not in available:
                    self._offline_controllers.append(item)
                    continue
                if any(p.port == port for p in self.panels):
                    continue
                device_type = item.get("device_type", NGC3_DEVICE_TYPE)
                panel_class = (ImpacPyrometerPanel if device_type in LEGACY_IMPAC_DEVICE_TYPES else
                               PfeifferDualGaugePanel if device_type == PFEIFFER_DEVICE_TYPE else
                               Agilent4UHVPanel if device_type == AGILENT_DEVICE_TYPE else
                               EpimaxPVCPanel if device_type == EPIMAX_DEVICE_TYPE else
                               TDKPanel if device_type == TDK_DEVICE_TYPE else DevicePanel)
                try:
                    panel = panel_class(self.scroller.inner, self,
                          name=item.get("name", port), port=port,
                          baudrate=int(item.get("baudrate", DEFAULT_BAUD)),
                          saved_state=item.get("state", {}) or {})
                    self.panels.append(panel)
                except Exception as exc:
                    self._offline_controllers.append(item)
                    print(f"Could not restore panel {port}: {exc}", file=sys.stderr)
            self.relayout_panels()
            self.refresh_alert_summary()
            if isinstance(layout, dict):
                try:
                    position = float(layout.get("scroll_fraction", 0))
                    if 0 <= position <= 1:
                        self.after(300, lambda: self.scroller.canvas.yview_moveto(position))
                except (ValueError, TypeError):
                    pass
        finally:
            self._profile_loading = False
        self._last_saved_config_text = "" if replace_existing else json.dumps(self._config_data(), indent=2, sort_keys=True)
        if replace_existing:
            self.save_config()

    def load_config(self):
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self._load_config_data(data)
        except Exception as exc:
            print(f"Failed to restore configuration: {exc}", file=sys.stderr)

    def open_profiles_window(self):
        win = tk.Toplevel(self)
        win.title("UHV Sentinel · Profiles")
        win.geometry("670x460")
        win.configure(background=UNIBAS_GRAY6)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)
        ttk.Label(win,text="Save or restore device connections, panel layout, theme, and graphs. Profiles never arm device outputs.",
           wraplength=640).grid(row=0,column=0,sticky="ew",padx=12,pady=10)
        frame=ttk.Frame(win,padding=12)
        frame.grid(row=1,column=0,sticky="nsew")
        frame.columnconfigure(0,weight=1)
        frame.rowconfigure(0,weight=1)
        lb=tk.Listbox(frame,exportselection=False, height=10)
        lb.grid(row=0,column=0,sticky="nsew")
        sb=ttk.Scrollbar(frame,command=lb.yview)
        sb.grid(row=0,column=1,sticky="ns")
        lb.configure(yscrollcommand=sb.set)
        selected=[]
        def refresh():
            selected.clear()
            lb.delete(0,"end")
            for path in sorted(profiles.profiles_dir().glob("*.json")):
                selected.append(path)
                lb.insert("end",path.stem)
        def current():
            if not lb.curselection():
                messagebox.showwarning("Select profile","Select a saved profile first.",parent=win)
                return None
            return selected[lb.curselection()[0]]
        def save():
            name=simpledialog.askstring("Save profile","Profile name:",parent=win)
            if not name:return
            try:
                path=profiles.profiles_dir()/(profiles.slug(name)+".json")
                if path.exists() and not messagebox.askyesno("Overwrite profile",f"Replace {path.name}?",parent=win):return
                selected_theme = resolve_theme_file(alert_manager.settings.theme_file)
                profiles.save_profile(name,self._config_data(),dest=path,theme_path=selected_theme)
                refresh()
            except Exception as exc:
                messagebox.showerror("Save profile failed",str(exc),parent=win)
        def load_path(path):
            try:
                payload=profiles.load_profile(path)
                config=payload["config"]
                available=set(self.port_combo["values"])
                missing=[entry for entry in config["controllers"] if entry["port"] not in available]
                if missing:
                    # Preserve missing devices and allow manual remapping when
                    # imported on a different computer (COM5 -> /dev/ttyUSB0).
                    if messagebox.askyesno("Missing serial ports",f"{len(missing)} saved port(s) are not connected.\nRemap them now?\n(No = keep offline.)",parent=win):
                        used={d["port"] for d in config["controllers"] if d not in missing}
                        for entry in missing:
                            new_port=simpledialog.askstring("Remap serial device",f"{entry['name']} · old: {entry['port']}\nEnter replacement port (empty to keep offline):",parent=win)
                            if new_port and new_port.strip() and new_port.strip() not in used:
                                used.add(new_port.strip())
                                entry["port"]=new_port.strip()
                if not messagebox.askyesno("Apply profile",f"Replace the current workspace with '{payload['name']}'?\nConnected devices will be disconnected and reconnected.\nNo power supply output will be changed.",parent=win):return
                new_theme = profiles.install_embedded_theme(payload)
                if new_theme:
                    config["alert_settings"]["theme_file"] = new_theme
                self._load_config_data(config,replace_existing=True)
                win.destroy()
            except Exception as exc:
                messagebox.showerror("Load profile failed",str(exc),parent=win)
        def import_profile():
            path=filedialog.askopenfilename(parent=win,title="Import profile",filetypes=[("UHV Sentinel profile","*.json")])
            if not path:return
            try:
                payload=profiles.load_profile(Path(path))
                target=profiles.profiles_dir()/(profiles.slug(payload["name"])+".json")
                if target.exists() and not messagebox.askyesno("Overwrite profile",f"Replace {target.name}?",parent=win):return
                profiles.save_profile_payload(payload,target)
                refresh()
            except Exception as exc:
                messagebox.showerror("Import failed",str(exc),parent=win)
        def export_profile():
            path=current()
            if path is None:return
            dest=filedialog.asksaveasfilename(parent=win,defaultextension=".json",initialfile=path.name,
                         filetypes=[("UHV Sentinel profile","*.json")])
            if not dest:return
            try:
                data=profiles.load_profile(path)
                profiles.save_profile_payload(data,Path(dest))
            except Exception as exc:
                messagebox.showerror("Export failed",str(exc),parent=win)
        def delete_profile():
            path=current()
            if path is None:return
            if messagebox.askyesno("Delete profile",f"Delete {path.name}?\nThis does not delete pressure history.",parent=win):
                path.unlink()
                refresh()
        actions=ttk.Frame(frame)
        actions.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(10,0))
        for title,callback in (("Save current",save),("Load selected",lambda:load_path(current()) if current() else None),
              ("Import...",import_profile),("Export...",export_profile),("Delete",delete_profile)):
            ttk.Button(actions,text=title,command=callback).pack(side="left",padx=3)
        ttk.Label(win,text=f"Stored in: {profiles.profiles_dir()}",wraplength=640).grid(row=2,column=0,sticky="ew",padx=12,pady=8)
        refresh()

    def on_close(self):
        if self._config_save_after is not None:
            try:
                self.after_cancel(self._config_save_after)
            except Exception:
                pass
            self._config_save_after = None
        for panel in list(self.panels):
            panel.stop_event.set()
        for panel in list(self.panels):
            if isinstance(panel, ImpacPyrometerPanel):
                # Wait briefly for its serial worker to release COM itself;
                # never race that thread with a GUI-thread close().
                if panel.thread.is_alive():
                    panel.thread.join(timeout=3.0)
                if panel.thread.is_alive():
                    continue  # It will release COM in its own finally block.
            try:
                panel.device.close()
            except Exception:
                pass
        self.save_config()
        self.web_monitor.stop()
        self.destroy()


def main():
    app = UHVSentinelApp()
    app.mainloop()
    if app.pending_update:
        # The GUI has fully released the serial ports and closed its database.
        # Runtime Python is outside the app tree, so files can be replaced safely.
        try:
            updater.apply_prepared_update(**app.pending_update)
        except Exception as exc:
            updater.write_update_result(False, str(exc))
            try:
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror("UHV Sentinel update failed", str(exc))
                root.destroy()
            except Exception:
                print(f"Update failed: {exc}", file=sys.stderr)
        finally:
            # Start the installed app again, whether update succeeded or failed.
            installed_script = install_dir() / "app" / "uhv_sentinel.py"
            if installed_script.is_file():
                python_exe = runtime_python(gui=True)
                if not python_exe.exists():
                    python_exe = runtime_python()
                subprocess.Popen([str(python_exe), str(installed_script)], cwd=str(installed_script.parent),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    main()
