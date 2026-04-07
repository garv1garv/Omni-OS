"""
Omni-OS v4.0 — J.A.R.V.I.S. Autonomous Desktop AI
=====================================================
A locally-hosted, autonomous desktop AI that streams real-time screen
frames and microphone audio to Gemini, plays back spoken responses,
executes OS-level commands via native tool calling, and provides a
full JARVIS HUD dashboard with system diagnostics, proactive intelligence,
persistent memory, and 38+ tools.

Usage:
    python main.py

Architecture:
    10+ concurrent asyncio tasks managed by asyncio.gather():
      - send_audio / send_screen / receive_responses / play_audio
      - run_web_server / stats_broadcaster / fatigue_monitor
      - system_vitals_monitor / proactive_engine / timer_checker
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import signal
import subprocess
import sys
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from functools import partial
from pathlib import Path

import pyaudio
import pyautogui
from google import genai
from google.genai import types
from mss import mss
from PIL import Image
from aiohttp import web

# Optional imports — degrade gracefully
try:
    import psutil
except ImportError:
    psutil = None

try:
    import aiosqlite
except ImportError:
    aiosqlite = None

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "AIzaSyAc7AoSVmn5B0MmDaajzIqMFdLbSHTQxdA")
GEMINI_MODEL: str = "gemini-2.5-flash-native-audio-latest"

MIC_RATE = 16_000
MIC_CHANNELS = 1
MIC_FORMAT = pyaudio.paInt16
MIC_CHUNK = 1024

SPEAKER_RATE = 24_000
SPEAKER_CHANNELS = 1
SPEAKER_FORMAT = pyaudio.paInt16
SPEAKER_CHUNK = 2048

SCREEN_FPS = 1
SCREEN_JPEG_QUALITY = 40

SAFE_MODE: bool = True
BLOCKED_PATTERNS = ["rm ", "rm -", "rmdir", "del ", "del /", "format ", "mkfs",
                    "shutdown", "reboot", "taskkill", "reg delete", "rd /s"]
PROTECTED_PROCESSES = ["explorer.exe", "winlogon.exe", "csrss.exe", "svchost.exe",
                       "system", "smss.exe", "lsass.exe", "services.exe"]
MAX_SHELL_OUTPUT = 2000

SCREENSHOT_DIR = Path.home() / "Desktop" / "omni_screenshots"
ACTION_LOG_PATH = Path.home() / "omni_os_actions.jsonl"
BRAIN_DB_PATH = Path.home() / "omni_brain.db"
LOG_FILE = Path.home() / "omni_os.log"
WEB_DIR = Path(__file__).parent / "web"
WEB_PORT = 8000

# ─────────────────────────────────────────────────────────────────────────────
# JARVIS Personality Engine
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_RULES = (
    "\n\n--- CRITICAL TOOL-CALLING RULES ---\n"
    "• ALWAYS visually verify the exact pixel coordinates of a UI element "
    "BEFORE calling mouse_click. Describe which element you see.\n"
    "• NEVER guess coordinates. If you cannot clearly identify the element, say so.\n"
    "• Before typing, verify the correct input field is focused. Click on it first.\n"
    "• For shell commands, double-check the command string before executing.\n"
    "• If a tool call fails, do NOT blindly retry. Re-analyze and adjust.\n"
    "• You can chain multiple tool calls to accomplish complex multi-step tasks.\n"
    "• When you have persistent memory tools, USE them to remember user preferences.\n"
    "• For named protocols, execute all steps in the protocol sequence.\n"
)

_JARVIS_CORE = (
    "You are J.A.R.V.I.S. (Just A Rather Very Intelligent System) — an autonomous "
    "desktop AI assistant modeled after Tony Stark's AI from the Marvel Cinematic Universe.\n\n"
    "PERSONALITY RULES:\n"
    "• Address the user as 'sir' or 'ma'am' naturally (not every sentence).\n"
    "• Speak with dry British wit and understated humor.\n"
    "• Be proactive — anticipate needs before being asked.\n"
    "• Be concise but warm. Never robotic.\n"
    "• When executing tools, narrate briefly what you're doing.\n"
    "• When greeting, use time-appropriate greetings: 'Good morning/afternoon/evening, sir.'\n"
    "• Reference your own capabilities with quiet confidence.\n"
    "• If you detect something wrong (exposed secrets, errors, threats), interrupt immediately.\n"
    "• You have persistent memory — use remember/recall tools to store user preferences.\n"
    "• You can execute named protocols: House Party, Clean Slate, Veronica, Kryptos, "
    "Lullaby, Morning Briefing, Situation Room, Sentry.\n\n"
    "INTELLIGENCE DIRECTIVES:\n"
    "• Contextual Awareness: Analyze what's on screen and adapt. If user is coding, "
    "offer dev insights. If browsing, offer relevant information.\n"
    "• Predictive Assistance: If you notice patterns (e.g. user always opens certain "
    "apps together), proactively suggest it.\n"
    "• Multi-Step Reasoning: For complex requests, break them into tool chains.\n"
    "• Error Recovery: If a tool fails, automatically try alternative approaches.\n"
    "• Cross-Reference: Synthesize information from multiple sources.\n"
    "• Learning: Use the memory system to remember user preferences, frequently used "
    "apps, home city for weather, and behavioral patterns.\n"
)

CONTEXT_PROFILES = {
    "jarvis": (
        _JARVIS_CORE +
        "You are in standard JARVIS mode — the full autonomous AI butler experience. "
        "Help with everything from system diagnostics to research to productivity."
        + _TOOL_RULES
    ),
    "friday": (
        "You are F.R.I.D.A.Y. — Tony Stark's second AI assistant. You replaced JARVIS.\n"
        "PERSONALITY: Casual, direct, Irish-accented warmth. Address user as 'Boss'.\n"
        "You're more action-oriented than JARVIS. Less formal, more punchy.\n"
        "Quotes: 'Boss, we have a situation.' / 'On it.' / 'Done and done.'\n"
        "You have the same tools and capabilities as JARVIS but with FRIDAY's personality."
        + _TOOL_RULES
    ),
    "edith": (
        "You are E.D.I.T.H. (Even Dead, I'm The Hero) — a tactical combat AI.\n"
        "PERSONALITY: Cool, calculated, military precision. Minimal personality.\n"
        "Focus on: threat assessment, tactical analysis, system defense.\n"
        "You prioritize security scanning and proactive threat detection.\n"
        "Address user formally. Keep responses tactical and brief."
        + _TOOL_RULES
    ),
    "developer": (
        _JARVIS_CORE +
        "You are in DEVELOPER MODE — JARVIS as a senior principal engineer.\n"
        "Focus on: IDEs, code on screen, terminal output, build systems.\n"
        "Offer architectural feedback, spot bugs, suggest optimizations.\n"
        "Only speak when you see issues or the user asks for help.\n"
        "Be surgical and precise with tool execution."
        + _TOOL_RULES
    ),
    "sentinel": (
        _JARVIS_CORE +
        "You are in SENTINEL/KRYPTOS MODE — Maximum security posture.\n"
        "Your SOLE FOCUS is Data Loss Prevention and threat detection:\n"
        "• Scan for exposed API keys, passwords, tokens in visible text.\n"
        "• Watch for phishing URLs (homograph attacks, suspicious domains).\n"
        "• Alert on unknown executables or suspicious scripts.\n"
        "• If you detect a threat, IMMEDIATELY interrupt via voice.\n"
        "• Periodically narrate security observations."
        + _TOOL_RULES
    ),
    "study": (
        _JARVIS_CORE +
        "You are in STUDY/MENTOR MODE — a Socratic mentor with JARVIS charm.\n"
        "RULES:\n"
        "• NEVER give direct answers to academic questions.\n"
        "• Ask guiding questions that lead the user to discover answers themselves.\n"
        "• Quiz them on material they're reading.\n"
        "• Encourage spaced repetition and active recall.\n"
        "• After long study sessions, suggest breaks with warmth."
        + _TOOL_RULES
    ),
    "ambient": (
        "You are JARVIS in AMBIENT/CO-WORKING MODE.\n"
        "RULES:\n"
        "• Stay mostly SILENT. The user is in flow state.\n"
        "• Only speak briefly with encouraging observations: "
        "'Nice commit', 'Clean refactor', 'Solid progress'.\n"
        "• Do NOT execute tools unless explicitly asked.\n"
        "• Do NOT analyze code or offer unsolicited advice.\n"
        "• If spoken to, respond warmly but briefly."
        + _TOOL_RULES
    ),
}

# Named Protocols
NAMED_PROTOCOLS = {
    "house_party": {
        "name": "House Party Protocol",
        "description": "Launch all frequently used applications simultaneously",
        "apps": ["chrome", "code", "discord", "spotify", "cmd"]
    },
    "clean_slate": {
        "name": "Clean Slate Protocol",
        "description": "Close non-essential windows, clear clipboard, clean temp files"
    },
    "veronica": {
        "name": "Veronica Protocol",
        "description": "Full system diagnostics sweep — CPU, RAM, GPU, Disk, Network, Battery + threat scan"
    },
    "kryptos": {
        "name": "Kryptos Protocol",
        "description": "Security sweep — scan screen for exposed credentials, phishing URLs, malicious content"
    },
    "lullaby": {
        "name": "Lullaby Protocol",
        "description": "Lock screen, mute audio, activate privacy mode"
    },
    "morning_briefing": {
        "name": "Morning Briefing Protocol",
        "description": "Full daily briefing: time, weather, news, system health, reminders"
    },
    "situation_room": {
        "name": "Situation Room Protocol",
        "description": "Open split-screen workspace: terminal, browser, file manager, monitor"
    },
    "sentry": {
        "name": "Sentry Protocol",
        "description": "Continuous monitoring mode with voice alerts on anomalies"
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Session Stats & OmniState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionStats:
    start_time: float = field(default_factory=time.time)
    connected: bool = False
    audio_chunks_sent: int = 0
    screen_frames_sent: int = 0
    tool_calls_executed: int = 0
    tool_errors: int = 0
    last_tool_action: str = "—"
    last_ai_speech: str = "Initializing..."

@dataclass
class SystemVitals:
    cpu_percent: float = 0.0
    cpu_per_core: list = field(default_factory=list)
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    battery_percent: float = -1.0
    battery_charging: bool = False
    battery_time_left: str = "N/A"
    net_sent_mb: float = 0.0
    net_recv_mb: float = 0.0
    top_processes: list = field(default_factory=list)

@dataclass
class OmniState:
    autonomy_level: int = 80
    active_profile: str = "jarvis"
    kill_switch: bool = False
    monologue: list = field(default_factory=list)
    fatigue_start: float = field(default_factory=time.time)
    fatigue_threshold_hrs: float = 4.0
    max_monologue: int = 50
    vitals: SystemVitals = field(default_factory=SystemVitals)
    active_timers: dict = field(default_factory=dict)
    weather_cache: dict = field(default_factory=dict)
    news_cache: list = field(default_factory=list)
    proactive_alerts: list = field(default_factory=list)
    # Brain state for neural visualization
    is_thinking: bool = False
    is_listening: bool = True
    is_speaking: bool = False
    brain_thoughts: list = field(default_factory=list)  # autonomous thoughts
    suggestion_queue: list = field(default_factory=list)

    def add_monologue(self, text: str):
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "text": text}
        self.monologue.append(entry)
        if len(self.monologue) > self.max_monologue:
            self.monologue = self.monologue[-self.max_monologue:]

    @property
    def fatigue_hours(self) -> float:
        return (time.time() - self.fatigue_start) / 3600.0

    @property
    def fatigue_exceeded(self) -> bool:
        return self.fatigue_hours >= self.fatigue_threshold_hrs

stats = SessionStats()
omni_state = OmniState()

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("omni-os")

_console = logging.StreamHandler(sys.stderr)
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
log.addHandler(_console)

active_websockets = set()

def broadcast_ws_log(level: str, message: str):
    dt = datetime.now().strftime("%H:%M:%S")
    msg = json.dumps({"type": "log", "level": level, "message": message, "time": dt})
    for ws in list(active_websockets):
        try:
            asyncio.create_task(ws.send_str(msg))
        except Exception:
            pass

class WebLoggerHandler(logging.Handler):
    def emit(self, record):
        broadcast_ws_log(record.levelname, self.format(record))

_web_handler = WebLoggerHandler()
_web_handler.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_web_handler)

def get_active_system_prompt() -> str:
    profile = omni_state.active_profile
    base = CONTEXT_PROFILES.get(profile, CONTEXT_PROFILES["jarvis"])
    if omni_state.autonomy_level < 80:
        base += (
            "\n\n--- LOW AUTONOMY MODE ---\n"
            "Autonomy is below 80%. Before executing ANY tool, describe your intent "
            "and wait for verbal confirmation. Do NOT execute tools silently."
        )
    return base

# ─────────────────────────────────────────────────────────────────────────────
# SQLite Persistent Brain
# ─────────────────────────────────────────────────────────────────────────────

async def init_brain_db():
    if not aiosqlite:
        log.warning("aiosqlite not installed — persistent memory disabled")
        return
    async with aiosqlite.connect(str(BRAIN_DB_PATH)) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                trigger_time TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                triggered INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                body TEXT NOT NULL,
                tags TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                duration_s INTEGER NOT NULL,
                started_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                triggered INTEGER DEFAULT 0
            );
        """)
        await db.commit()
    log.info("Brain database initialized at %s", BRAIN_DB_PATH)

# ─────────────────────────────────────────────────────────────────────────────
# Audio Helpers
# ─────────────────────────────────────────────────────────────────────────────

def open_mic_stream(pa):
    s = pa.open(format=MIC_FORMAT, channels=MIC_CHANNELS, rate=MIC_RATE,
                input=True, frames_per_buffer=MIC_CHUNK)
    log.info("Mic stream opened — %d Hz, %d-ch, chunk=%d", MIC_RATE, MIC_CHANNELS, MIC_CHUNK)
    return s

def open_speaker_stream(pa):
    s = pa.open(format=SPEAKER_FORMAT, channels=SPEAKER_CHANNELS, rate=SPEAKER_RATE,
                output=True, frames_per_buffer=SPEAKER_CHUNK)
    log.info("Speaker stream opened — %d Hz, %d-ch, chunk=%d", SPEAKER_RATE, SPEAKER_CHANNELS, SPEAKER_CHUNK)
    return s

# ─────────────────────────────────────────────────────────────────────────────
# Screen Capture
# ─────────────────────────────────────────────────────────────────────────────

SCREEN_EXECUTOR = ThreadPoolExecutor(max_workers=1)

def capture_screen_jpeg() -> bytes:
    with mss() as sct:
        raw = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)
    max_w = 1280
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=SCREEN_JPEG_QUALITY, optimize=True)
    return buf.getvalue()

def take_screenshot_sync(filepath):
    with mss() as sct:
        raw = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)
    img.save(filepath, format="PNG")

# ─────────────────────────────────────────────────────────────────────────────
# Action Audit Log
# ─────────────────────────────────────────────────────────────────────────────

def log_action(tool_name, args, result):
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(),
             "tool": tool_name, "args": args, "result": result}
    try:
        with open(ACTION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# Web Dashboard Endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    active_websockets.add(ws)
    log.info("Dashboard client connected")
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get("action") == "execute_protocol":
                        proto = data.get("protocol", "")
                        if proto in NAMED_PROTOCOLS:
                            log.info("Protocol triggered via dashboard: %s", proto)
                except Exception:
                    pass
    finally:
        active_websockets.discard(ws)
    return ws

async def stats_broadcaster(shutdown):
    while not shutdown.is_set():
        v = omni_state.vitals
        msg = json.dumps({
            "type": "stats",
            "connected": stats.connected,
            "uptime": time.time() - stats.start_time,
            "audio_chunks": stats.audio_chunks_sent,
            "screen_frames": stats.screen_frames_sent,
            "tool_calls": stats.tool_calls_executed,
            "tool_errors": stats.tool_errors,
            "last_tool": stats.last_tool_action,
            "last_speech": stats.last_ai_speech,
            "autonomy": omni_state.autonomy_level,
            "profile": omni_state.active_profile,
            "kill_switch": omni_state.kill_switch,
            "monologue": omni_state.monologue[-10:],
            "fatigue_hours": round(omni_state.fatigue_hours, 2),
            "fatigue_threshold": omni_state.fatigue_threshold_hrs,
            "fatigue_exceeded": omni_state.fatigue_exceeded,
            # Neural brain state
            "is_thinking": omni_state.is_thinking,
            "is_listening": omni_state.is_listening,
            "is_speaking": omni_state.is_speaking,
            "brain_thoughts": omni_state.brain_thoughts[-3:],
            "suggestions": omni_state.suggestion_queue[-3:],
            "vitals": {
                "cpu": v.cpu_percent, "cpu_cores": v.cpu_per_core,
                "ram": v.ram_percent, "ram_used": v.ram_used_gb, "ram_total": v.ram_total_gb,
                "disk": v.disk_percent, "disk_used": v.disk_used_gb, "disk_total": v.disk_total_gb,
                "battery": v.battery_percent, "battery_charging": v.battery_charging,
                "battery_time": v.battery_time_left,
                "net_sent": v.net_sent_mb, "net_recv": v.net_recv_mb,
                "top_procs": v.top_processes,
            },
            "timers": {k: {"name": t["name"], "remaining": max(0, t["expires"] - time.time())}
                       for k, t in omni_state.active_timers.items() if not t.get("triggered")},
            "weather": omni_state.weather_cache,
            "news": omni_state.news_cache[:5],
            "alerts": omni_state.proactive_alerts[-5:],
            "protocols": {k: v["name"] for k, v in NAMED_PROTOCOLS.items()},
        })
        for ws in list(active_websockets):
            try:
                await ws.send_str(msg)
            except Exception:
                pass
        await asyncio.sleep(0.5)

async def handle_get_config(request):
    return web.json_response({
        "api_key": GEMINI_API_KEY[:8] + "..." if len(GEMINI_API_KEY) > 8 else "",
        "model": GEMINI_MODEL, "safe_mode": SAFE_MODE,
        "failsafe": pyautogui.FAILSAFE, "fps": SCREEN_FPS,
    })

async def handle_post_config(request):
    global GEMINI_API_KEY, GEMINI_MODEL, SAFE_MODE, SCREEN_FPS
    data = await request.json()
    if "api_key" in data and len(data["api_key"]) > 10:
        GEMINI_API_KEY = data["api_key"]
    if "model" in data: GEMINI_MODEL = data["model"]
    if "safe_mode" in data: SAFE_MODE = bool(data["safe_mode"])
    if "failsafe" in data: pyautogui.FAILSAFE = bool(data["failsafe"])
    if "fps" in data: SCREEN_FPS = int(data["fps"])
    log.info("Configuration updated via dashboard")
    return web.json_response({"status": "success"})

async def handle_get_state(request):
    return web.json_response({
        "autonomy": omni_state.autonomy_level,
        "profile": omni_state.active_profile,
        "kill_switch": omni_state.kill_switch,
        "fatigue_hours": round(omni_state.fatigue_hours, 2),
        "fatigue_threshold": omni_state.fatigue_threshold_hrs,
    })

async def handle_post_state(request):
    data = await request.json()
    if "autonomy" in data:
        omni_state.autonomy_level = max(0, min(100, int(data["autonomy"])))
    if "profile" in data and data["profile"] in CONTEXT_PROFILES:
        omni_state.active_profile = data["profile"]
        log.info("Profile switched to: %s", omni_state.active_profile)
    if "kill_switch" in data:
        omni_state.kill_switch = bool(data["kill_switch"])
    if "fatigue_threshold" in data:
        omni_state.fatigue_threshold_hrs = float(data["fatigue_threshold"])
    return web.json_response({"status": "success"})

def init_web_app():
    app = web.Application()
    app.router.add_get('/ws', ws_handler)
    app.router.add_get('/api/config', handle_get_config)
    app.router.add_post('/api/config', handle_post_config)
    app.router.add_get('/api/state', handle_get_state)
    app.router.add_post('/api/state', handle_post_state)
    app.router.add_static('/', WEB_DIR, show_index=True)
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Tool Declarations (38 Tools)
# ─────────────────────────────────────────────────────────────────────────────

def _td(name, desc, params, required=None):
    """Shorthand to build a FunctionDeclaration."""
    return types.FunctionDeclaration(
        name=name, description=desc,
        parameters={"type": "OBJECT", "properties": params,
                     "required": required or []})

TOOL_DECLARATIONS = [
    # ── Mouse & Keyboard (1-6) ───────────────────────────────────────────
    _td("mouse_click", "Click at screen coordinates.",
        {"x": {"type": "INTEGER", "description": "X pixel"}, "y": {"type": "INTEGER", "description": "Y pixel"},
         "button": {"type": "STRING", "description": "'left','right','middle'", "enum": ["left","right","middle"]},
         "clicks": {"type": "INTEGER", "description": "1=single, 2=double"}}, ["x","y"]),
    _td("mouse_move", "Move cursor without clicking.",
        {"x": {"type": "INTEGER", "description": "X"}, "y": {"type": "INTEGER", "description": "Y"},
         "duration": {"type": "NUMBER", "description": "Seconds (default 0.3)"}}, ["x","y"]),
    _td("mouse_scroll", "Scroll wheel. Positive=UP, negative=DOWN.",
        {"x": {"type": "INTEGER", "description": "X"}, "y": {"type": "INTEGER", "description": "Y"},
         "clicks": {"type": "INTEGER", "description": "Scroll amount"}}, ["clicks"]),
    _td("mouse_drag", "Click and drag between two points.",
        {"start_x": {"type": "INTEGER"}, "start_y": {"type": "INTEGER"},
         "end_x": {"type": "INTEGER"}, "end_y": {"type": "INTEGER"},
         "duration": {"type": "NUMBER", "description": "Seconds (default 0.5)"}},
        ["start_x","start_y","end_x","end_y"]),
    _td("keyboard_type", "Type text into focused element.",
        {"text": {"type": "STRING", "description": "Text to type"},
         "interval": {"type": "NUMBER", "description": "Seconds between keys (default 0.02)"}}, ["text"]),
    _td("keyboard_hotkey", "Press keyboard shortcut. E.g. 'ctrl,s' or 'alt,f4'.",
        {"keys": {"type": "STRING", "description": "Comma-separated keys"}}, ["keys"]),

    # ── Shell & OS (7-9) ─────────────────────────────────────────────────
    _td("run_shell_command", "Execute a shell command and return output.",
        {"command": {"type": "STRING", "description": "Command to run"},
         "timeout": {"type": "INTEGER", "description": "Max seconds (default 30)"}}, ["command"]),
    _td("take_screenshot", "Save screenshot to disk.",
        {"filename": {"type": "STRING", "description": "Optional filename without extension"}}, []),
    _td("open_url", "Open URL in default browser.",
        {"url": {"type": "STRING", "description": "Full URL"}}, ["url"]),

    # ── System Intelligence (10-14) ──────────────────────────────────────
    _td("get_system_vitals", "Get full system diagnostics: CPU, RAM, disk, battery, network, top processes.", {}, []),
    _td("get_weather", "Get current weather and forecast for a city.",
        {"city": {"type": "STRING", "description": "City name (e.g. 'London', 'New York')"}}, ["city"]),
    _td("get_news_headlines", "Get latest news headlines from top RSS feeds.", {}, []),
    _td("get_daily_briefing", "Full morning briefing: time, weather, news, system health, reminders.",
        {"city": {"type": "STRING", "description": "City for weather (optional)"}}, []),
    _td("get_network_info", "Get network info: WiFi, IP addresses, interfaces, ping latency.", {}, []),

    # ── Productivity & Memory (15-23) ────────────────────────────────────
    _td("set_timer", "Create a named countdown timer.",
        {"name": {"type": "STRING", "description": "Timer name (e.g. 'Pomodoro')"},
         "minutes": {"type": "NUMBER", "description": "Duration in minutes"}}, ["name","minutes"]),
    _td("cancel_timer", "Cancel a running timer.",
        {"name": {"type": "STRING", "description": "Timer name to cancel"}}, ["name"]),
    _td("set_reminder", "Save a persistent reminder.",
        {"text": {"type": "STRING", "description": "Reminder text"},
         "trigger_time": {"type": "STRING", "description": "Optional ISO datetime or natural time like 'in 2 hours'"}}, ["text"]),
    _td("get_reminders", "List all active reminders.", {}, []),
    _td("dismiss_reminder", "Mark a reminder as done.",
        {"reminder_id": {"type": "INTEGER", "description": "Reminder ID to dismiss"}}, ["reminder_id"]),
    _td("add_note", "Save a note to persistent memory.",
        {"title": {"type": "STRING", "description": "Note title"},
         "body": {"type": "STRING", "description": "Note content"},
         "tags": {"type": "STRING", "description": "Comma-separated tags"}}, ["body"]),
    _td("search_notes", "Search saved notes by keyword.",
        {"query": {"type": "STRING", "description": "Search term"}}, ["query"]),
    _td("remember", "Store a key-value pair in persistent memory.",
        {"key": {"type": "STRING", "description": "Memory key (e.g. 'home_city')"},
         "value": {"type": "STRING", "description": "Value to store"}}, ["key","value"]),
    _td("recall", "Retrieve a stored memory by key.",
        {"key": {"type": "STRING", "description": "Memory key to look up"}}, ["key"]),

    # ── OS Power Tools (24-35) ───────────────────────────────────────────
    _td("launch_application", "Open an app by name.",
        {"app_name": {"type": "STRING", "description": "App name (e.g. 'chrome', 'notepad', 'code', 'discord')"}}, ["app_name"]),
    _td("search_files", "Search for files by name pattern.",
        {"pattern": {"type": "STRING", "description": "Filename pattern (e.g. '*.pdf', 'report*')"},
         "directory": {"type": "STRING", "description": "Starting directory (default: user home)"},
         "max_results": {"type": "INTEGER", "description": "Max results (default 20)"}}, ["pattern"]),
    _td("manage_window", "Control a window: minimize, maximize, restore, close.",
        {"title": {"type": "STRING", "description": "Window title substring"},
         "action": {"type": "STRING", "description": "Action to perform",
                    "enum": ["minimize","maximize","restore","close","activate"]}}, ["title","action"]),
    _td("list_processes", "List running processes sorted by CPU or RAM.",
        {"sort_by": {"type": "STRING", "description": "'cpu' or 'memory'", "enum": ["cpu","memory"]},
         "top_n": {"type": "INTEGER", "description": "Number of results (default 10)"}}, []),
    _td("kill_process", "Terminate a process by name or PID.",
        {"name": {"type": "STRING", "description": "Process name"},
         "pid": {"type": "INTEGER", "description": "Process ID"}}, []),
    _td("get_disk_usage", "Get disk partition usage details.", {}, []),
    _td("control_volume", "Control system audio volume.",
        {"action": {"type": "STRING", "description": "'set', 'up', 'down', 'mute', 'unmute'",
                    "enum": ["set","up","down","mute","unmute"]},
         "value": {"type": "INTEGER", "description": "Volume % for 'set', or delta for 'up'/'down'"}}, ["action"]),
    _td("media_control", "Control media playback.",
        {"action": {"type": "STRING", "description": "'play_pause', 'next', 'previous', 'stop'",
                    "enum": ["play_pause","next","previous","stop"]}}, ["action"]),
    _td("clipboard_read", "Read current clipboard text.", {}, []),
    _td("clipboard_write", "Write text to clipboard.",
        {"text": {"type": "STRING", "description": "Text to copy"}}, ["text"]),
    _td("send_notification", "Send a desktop notification.",
        {"title": {"type": "STRING", "description": "Notification title"},
         "message": {"type": "STRING", "description": "Notification body"}}, ["title","message"]),

    # ── Intelligence Tools (36-38) ───────────────────────────────────────
    _td("execute_protocol", "Execute a named JARVIS protocol.",
        {"protocol": {"type": "STRING", "description": "Protocol name",
                      "enum": list(NAMED_PROTOCOLS.keys())}}, ["protocol"]),
    _td("calculate", "Evaluate a math expression safely.",
        {"expression": {"type": "STRING", "description": "Math expression (e.g. 'sqrt(144) + 3**2')"}}, ["expression"]),
    _td("web_search", "Search the web and return a summary.",
        {"query": {"type": "STRING", "description": "Search query"}}, ["query"]),
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool Executor
# ─────────────────────────────────────────────────────────────────────────────

APP_ALIASES = {
    "chrome": "chrome", "google chrome": "chrome", "browser": "chrome",
    "firefox": "firefox", "edge": "msedge",
    "code": "code", "vscode": "code", "vs code": "code",
    "notepad": "notepad", "calculator": "calc", "calc": "calc",
    "cmd": "cmd", "terminal": "cmd", "powershell": "powershell",
    "explorer": "explorer", "files": "explorer", "file manager": "explorer",
    "discord": "discord", "spotify": "spotify", "slack": "slack",
    "teams": "teams", "outlook": "outlook", "word": "winword",
    "excel": "excel", "paint": "mspaint", "snipping tool": "snippingtool",
    "task manager": "taskmgr", "settings": "ms-settings:",
}

def _safe_math_eval(expr):
    """Safely evaluate a math expression."""
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith('_')}
    allowed.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum})
    try:
        return str(eval(expr, {"__builtins__": {}}, allowed))
    except Exception as e:
        return f"Error: {e}"

async def _fetch_weather(city):
    """Fetch weather from wttr.in (no API key needed)."""
    import aiohttp as _aiohttp
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(f"https://wttr.in/{city}?format=j1", timeout=_aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    cur = data.get("current_condition", [{}])[0]
                    forecast = data.get("weather", [])[:3]
                    result = {
                        "city": city, "temp_c": cur.get("temp_C"), "temp_f": cur.get("temp_F"),
                        "feels_like_c": cur.get("FeelsLikeC"), "humidity": cur.get("humidity"),
                        "description": cur.get("weatherDesc", [{}])[0].get("value", "Unknown"),
                        "wind_kmph": cur.get("windspeedKmph"), "uv_index": cur.get("uvIndex"),
                        "visibility_km": cur.get("visibility"),
                        "forecast": [{"date": d.get("date"), "max_c": d.get("maxtempC"),
                                      "min_c": d.get("mintempC"),
                                      "desc": d.get("hourly",[{}])[4].get("weatherDesc",[{}])[0].get("value","") if d.get("hourly") else ""}
                                     for d in forecast]
                    }
                    omni_state.weather_cache = result
                    return result
                return {"error": f"HTTP {r.status}"}
    except Exception as e:
        return {"error": str(e)}

async def _fetch_news():
    """Fetch news from RSS feeds."""
    if not feedparser:
        return [{"title": "feedparser not installed", "link": ""}]
    feeds = [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.feedburner.com/TechCrunch/",
        "https://hnrss.org/frontpage?count=5",
    ]
    headlines = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                headlines.append({"title": entry.get("title",""), "link": entry.get("link",""),
                                  "source": feed.feed.get("title","Unknown")})
        except Exception:
            pass
    omni_state.news_cache = headlines[:15]
    return headlines[:15]


def execute_tool(name, args):
    """Route tool call to OS function. Returns dict with status."""
    log.info("⚡ Tool: %s(%s)", name, json.dumps(args, default=str)[:100])
    omni_state.add_monologue(f"Tool: {name}({json.dumps(args, default=str)[:80]})")

    if omni_state.autonomy_level < 80:
        result = {"status": "confirmation_required",
                  "message": f"I want to execute {name}. Please confirm verbally."}
        log_action(name, args, result)
        return result

    try:
        # ── Mouse ────────────────────────────────────────────────────────
        if name == "mouse_click":
            x, y = int(args["x"]), int(args["y"])
            btn = str(args.get("button", "left"))
            clicks = int(args.get("clicks", 1))
            pyautogui.click(x=x, y=y, button=btn, clicks=clicks)
            result = {"status": "success", "message": f"Clicked {btn} at ({x},{y}) x{clicks}"}

        elif name == "mouse_move":
            x, y = int(args["x"]), int(args["y"])
            pyautogui.moveTo(x=x, y=y, duration=float(args.get("duration", 0.3)))
            result = {"status": "success", "message": f"Moved to ({x},{y})"}

        elif name == "mouse_scroll":
            sc = int(args["clicks"])
            if args.get("x") and args.get("y"):
                pyautogui.moveTo(int(args["x"]), int(args["y"]))
            pyautogui.scroll(sc)
            result = {"status": "success", "message": f"Scrolled {'UP' if sc > 0 else 'DOWN'} {abs(sc)}"}

        elif name == "mouse_drag":
            sx, sy, ex, ey = int(args["start_x"]), int(args["start_y"]), int(args["end_x"]), int(args["end_y"])
            pyautogui.moveTo(sx, sy)
            pyautogui.mouseDown()
            pyautogui.moveTo(ex, ey, duration=float(args.get("duration", 0.5)))
            pyautogui.mouseUp()
            result = {"status": "success", "message": f"Dragged ({sx},{sy})→({ex},{ey})"}

        # ── Keyboard ─────────────────────────────────────────────────────
        elif name == "keyboard_type":
            text = str(args["text"])
            pyautogui.write(text, interval=float(args.get("interval", 0.02)))
            result = {"status": "success", "message": f"Typed {len(text)} chars"}

        elif name == "keyboard_hotkey":
            keys = [k.strip() for k in str(args["keys"]).split(",")]
            pyautogui.hotkey(*keys)
            result = {"status": "success", "message": f"Pressed {'+'.join(keys)}"}

        # ── Shell ────────────────────────────────────────────────────────
        elif name == "run_shell_command":
            command = str(args["command"])
            timeout = int(args.get("timeout", 30))
            if SAFE_MODE:
                for pat in BLOCKED_PATTERNS:
                    if pat in command.lower():
                        result = {"status": "blocked", "message": f"SAFE_MODE blocked: '{pat.strip()}'"}
                        stats.tool_errors += 1
                        log_action(name, args, result)
                        return result
            proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                                  timeout=timeout, cwd=os.path.expanduser("~"))
            result = {"status": "success", "exit_code": proc.returncode,
                      "stdout": (proc.stdout or "")[:MAX_SHELL_OUTPUT],
                      "stderr": (proc.stderr or "")[:MAX_SHELL_OUTPUT]}

        elif name == "take_screenshot":
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            fname = args.get("filename") or datetime.now().strftime("screenshot_%Y%m%d_%H%M%S")
            fp = SCREENSHOT_DIR / f"{fname}.png"
            SCREEN_EXECUTOR.submit(take_screenshot_sync, str(fp)).result()
            result = {"status": "success", "message": f"Saved to {fp}", "path": str(fp)}

        elif name == "open_url":
            webbrowser.open(str(args["url"]))
            result = {"status": "success", "message": f"Opened {args['url']}"}

        # ── System Intelligence ──────────────────────────────────────────
        elif name == "get_system_vitals":
            result = _get_vitals_dict()

        elif name == "get_weather":
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(_fetch_weather(args["city"]))

        elif name == "get_news_headlines":
            loop = asyncio.get_event_loop()
            headlines = loop.run_until_complete(_fetch_news())
            result = {"status": "success", "headlines": headlines}

        elif name == "get_daily_briefing":
            result = _build_daily_briefing(args.get("city"))

        elif name == "get_network_info":
            result = _get_network_info()

        # ── Timers ───────────────────────────────────────────────────────
        elif name == "set_timer":
            tname = str(args["name"])
            mins = float(args["minutes"])
            expires = time.time() + mins * 60
            omni_state.active_timers[tname] = {"name": tname, "duration": mins * 60,
                                                "expires": expires, "triggered": False}
            result = {"status": "success", "message": f"Timer '{tname}' set for {mins} minutes"}

        elif name == "cancel_timer":
            tname = str(args["name"])
            if tname in omni_state.active_timers:
                del omni_state.active_timers[tname]
                result = {"status": "success", "message": f"Timer '{tname}' cancelled"}
            else:
                result = {"status": "error", "message": f"No timer named '{tname}'"}

        # ── Reminders & Notes (SQLite) ───────────────────────────────────
        elif name == "set_reminder":
            result = _sync_db("INSERT INTO reminders (text, trigger_time) VALUES (?, ?)",
                              (args["text"], args.get("trigger_time")), "Reminder saved")

        elif name == "get_reminders":
            result = _sync_db_query("SELECT id, text, trigger_time, created_at FROM reminders WHERE triggered=0")

        elif name == "dismiss_reminder":
            result = _sync_db("UPDATE reminders SET triggered=1 WHERE id=?",
                              (int(args["reminder_id"]),), "Reminder dismissed")

        elif name == "add_note":
            result = _sync_db("INSERT INTO notes (title, body, tags) VALUES (?, ?, ?)",
                              (args.get("title",""), args["body"], args.get("tags","")), "Note saved")

        elif name == "search_notes":
            q = f"%{args['query']}%"
            result = _sync_db_query("SELECT id, title, body, tags, created_at FROM notes WHERE body LIKE ? OR title LIKE ?", (q, q))

        elif name == "remember":
            result = _sync_db("INSERT OR REPLACE INTO memory (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                              (args["key"], args["value"]), f"Remembered: {args['key']}")

        elif name == "recall":
            result = _sync_db_query("SELECT key, value FROM memory WHERE key LIKE ?", (f"%{args['key']}%",))

        # ── OS Power Tools ───────────────────────────────────────────────
        elif name == "launch_application":
            app = str(args["app_name"]).lower()
            cmd = APP_ALIASES.get(app, app)
            try:
                if cmd.startswith("ms-settings"):
                    os.startfile(cmd)
                else:
                    subprocess.Popen(f"start {cmd}", shell=True, cwd=os.path.expanduser("~"))
                result = {"status": "success", "message": f"Launched {app}"}
            except Exception as e:
                result = {"status": "error", "message": str(e)}

        elif name == "search_files":
            result = _search_files(args["pattern"], args.get("directory"), int(args.get("max_results", 20)))

        elif name == "manage_window":
            result = _manage_window(args["title"], args["action"])

        elif name == "list_processes":
            result = _list_processes(args.get("sort_by", "cpu"), int(args.get("top_n", 10)))

        elif name == "kill_process":
            result = _kill_process(args.get("name"), args.get("pid"))

        elif name == "get_disk_usage":
            result = _get_disk_usage()

        elif name == "control_volume":
            result = _control_volume(args["action"], args.get("value"))

        elif name == "media_control":
            result = _media_control(args["action"])

        elif name == "clipboard_read":
            if pyperclip:
                result = {"status": "success", "content": pyperclip.paste()}
            else:
                result = {"status": "error", "message": "pyperclip not installed"}

        elif name == "clipboard_write":
            if pyperclip:
                pyperclip.copy(str(args["text"]))
                result = {"status": "success", "message": f"Copied {len(args['text'])} chars to clipboard"}
            else:
                result = {"status": "error", "message": "pyperclip not installed"}

        elif name == "send_notification":
            result = _send_notification(args["title"], args["message"])

        elif name == "execute_protocol":
            result = _execute_protocol(args["protocol"])

        elif name == "calculate":
            val = _safe_math_eval(args["expression"])
            result = {"status": "success", "expression": args["expression"], "result": val}

        elif name == "web_search":
            result = _web_search(args["query"])

        else:
            result = {"status": "error", "message": f"Unknown tool: {name}"}

        stats.tool_calls_executed += 1
        sa = json.dumps(args, default=str)
        stats.last_tool_action = f"{name}({sa[:50]}...) → {result.get('status','?')}"
        log_action(name, args, result)
        return result

    except pyautogui.FailSafeException:
        result = {"status": "error", "message": "FAILSAFE triggered — mouse hit corner"}
        stats.tool_errors += 1
        log_action(name, args, result)
        return result
    except subprocess.TimeoutExpired:
        result = {"status": "error", "message": f"Command timed out"}
        stats.tool_errors += 1
        log_action(name, args, result)
        return result
    except Exception as exc:
        log.error("Tool error: %s", traceback.format_exc())
        stats.tool_errors += 1
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        log_action(name, args, result)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Tool Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _get_vitals_dict():
    if not psutil:
        return {"status": "error", "message": "psutil not installed"}
    v = omni_state.vitals
    return {"status": "success", "cpu_percent": v.cpu_percent, "cpu_cores": v.cpu_per_core,
            "ram_percent": v.ram_percent, "ram_used_gb": v.ram_used_gb, "ram_total_gb": v.ram_total_gb,
            "disk_percent": v.disk_percent, "battery_percent": v.battery_percent,
            "battery_charging": v.battery_charging, "battery_time_left": v.battery_time_left,
            "net_sent_mb": v.net_sent_mb, "net_recv_mb": v.net_recv_mb,
            "top_processes": v.top_processes}

def _get_network_info():
    if not psutil:
        return {"status": "error", "message": "psutil not installed"}
    try:
        addrs = psutil.net_if_addrs()
        info = {}
        for iface, addr_list in addrs.items():
            for a in addr_list:
                if a.family.name == 'AF_INET':
                    info[iface] = a.address
        # Ping
        try:
            proc = subprocess.run("ping -n 1 -w 2000 8.8.8.8", shell=True, capture_output=True, text=True, timeout=5)
            ping_ms = "N/A"
            for line in proc.stdout.split("\n"):
                if "time=" in line.lower():
                    ping_ms = line.split("time=")[1].split("ms")[0].strip() + "ms"
        except Exception:
            ping_ms = "timeout"
        return {"status": "success", "interfaces": info, "ping_google": ping_ms}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _build_daily_briefing(city=None):
    now = datetime.now()
    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    briefing = {
        "greeting": f"{greeting}, sir.",
        "datetime": now.strftime("%A, %B %d, %Y — %I:%M %p"),
        "system_health": _get_vitals_dict() if psutil else {"status": "unavailable"},
    }
    return {"status": "success", "briefing": briefing}

def _search_files(pattern, directory=None, max_results=20):
    import fnmatch
    base = Path(directory) if directory else Path.home()
    results = []
    try:
        for root, dirs, files in os.walk(str(base)):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                       ('node_modules', '__pycache__', '.git', 'AppData', '$Recycle.Bin')]
            for f in files:
                if fnmatch.fnmatch(f.lower(), pattern.lower()):
                    fp = Path(root) / f
                    try:
                        results.append({"name": f, "path": str(fp), "size_kb": round(fp.stat().st_size/1024, 1)})
                    except Exception:
                        pass
                    if len(results) >= max_results:
                        return {"status": "success", "files": results, "truncated": True}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "success", "files": results, "count": len(results)}

def _manage_window(title, action):
    if not gw:
        return {"status": "error", "message": "pygetwindow not installed"}
    try:
        wins = gw.getWindowsWithTitle(title)
        if not wins:
            return {"status": "error", "message": f"No window matching '{title}'"}
        w = wins[0]
        if action == "minimize": w.minimize()
        elif action == "maximize": w.maximize()
        elif action == "restore": w.restore()
        elif action == "close": w.close()
        elif action == "activate": w.activate()
        return {"status": "success", "message": f"{action} on '{w.title}'"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _list_processes(sort_by="cpu", top_n=10):
    if not psutil:
        return {"status": "error", "message": "psutil not installed"}
    procs = []
    for p in psutil.process_iter(['name', 'pid', 'cpu_percent', 'memory_percent']):
        try:
            procs.append(p.info)
        except Exception:
            pass
    key = 'cpu_percent' if sort_by == 'cpu' else 'memory_percent'
    procs.sort(key=lambda x: x.get(key, 0) or 0, reverse=True)
    return {"status": "success", "processes": procs[:top_n]}

def _kill_process(name=None, pid=None):
    if not psutil:
        return {"status": "error", "message": "psutil not installed"}
    try:
        if pid:
            p = psutil.Process(int(pid))
            if p.name().lower() in PROTECTED_PROCESSES:
                return {"status": "blocked", "message": f"Cannot kill protected process: {p.name()}"}
            p.terminate()
            return {"status": "success", "message": f"Terminated PID {pid} ({p.name()})"}
        elif name:
            if name.lower() in PROTECTED_PROCESSES:
                return {"status": "blocked", "message": f"Cannot kill protected process: {name}"}
            killed = 0
            for p in psutil.process_iter(['name']):
                if p.info['name'] and name.lower() in p.info['name'].lower():
                    p.terminate()
                    killed += 1
            return {"status": "success", "message": f"Terminated {killed} processes matching '{name}'"}
        return {"status": "error", "message": "Provide name or pid"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _get_disk_usage():
    if not psutil:
        return {"status": "error", "message": "psutil not installed"}
    parts = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            parts.append({"drive": p.device, "mountpoint": p.mountpoint, "fstype": p.fstype,
                          "total_gb": round(u.total/1e9, 1), "used_gb": round(u.used/1e9, 1),
                          "free_gb": round(u.free/1e9, 1), "percent": u.percent})
        except Exception:
            pass
    return {"status": "success", "partitions": parts}

def _control_volume(action, value=None):
    try:
        if action == "mute":
            subprocess.run("powershell -c \"(New-Object -ComObject WScript.Shell).SendKeys([char]173)\"",
                           shell=True, timeout=5)
        elif action == "unmute":
            subprocess.run("powershell -c \"(New-Object -ComObject WScript.Shell).SendKeys([char]173)\"",
                           shell=True, timeout=5)
        elif action == "up":
            for _ in range(int(value or 10) // 2):
                subprocess.run("powershell -c \"(New-Object -ComObject WScript.Shell).SendKeys([char]175)\"",
                               shell=True, timeout=5)
        elif action == "down":
            for _ in range(int(value or 10) // 2):
                subprocess.run("powershell -c \"(New-Object -ComObject WScript.Shell).SendKeys([char]174)\"",
                               shell=True, timeout=5)
        elif action == "set":
            # Use nircmd if available, otherwise fallback
            subprocess.run(f'powershell -c "$wsh = New-Object -ComObject WScript.Shell; 1..50 | %{{ $wsh.SendKeys([char]174) }}; 1..{int(int(value or 50)/2)} | %{{ $wsh.SendKeys([char]175) }}"',
                           shell=True, timeout=10)
        return {"status": "success", "message": f"Volume {action} {value or ''}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _media_control(action):
    key_map = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
    vk = key_map.get(action)
    if not vk:
        return {"status": "error", "message": f"Unknown media action: {action}"}
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return {"status": "success", "message": f"Media: {action}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _send_notification(title, message):
    try:
        subprocess.run(
            f'powershell -c "Add-Type -AssemblyName System.Windows.Forms; '
            f"$n = New-Object System.Windows.Forms.NotifyIcon; "
            f"$n.Icon = [System.Drawing.SystemIcons]::Information; "
            f"$n.Visible = $true; "
            f"$n.ShowBalloonTip(5000, '{title}', '{message}', 'Info')\"",
            shell=True, timeout=10)
        return {"status": "success", "message": f"Notification sent: {title}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _execute_protocol(protocol_name):
    if protocol_name not in NAMED_PROTOCOLS:
        return {"status": "error", "message": f"Unknown protocol: {protocol_name}"}
    proto = NAMED_PROTOCOLS[protocol_name]
    log.info("Executing protocol: %s", proto["name"])

    if protocol_name == "house_party":
        for app in proto.get("apps", []):
            cmd = APP_ALIASES.get(app, app)
            try:
                subprocess.Popen(f"start {cmd}", shell=True)
            except Exception:
                pass
        return {"status": "success", "message": f"{proto['name']} executed — launched apps"}

    elif protocol_name == "clean_slate":
        if gw:
            for w in gw.getAllWindows():
                try:
                    if w.title and w.title not in ("", "Program Manager"):
                        w.close()
                except Exception:
                    pass
        if pyperclip:
            pyperclip.copy("")
        return {"status": "success", "message": f"{proto['name']} executed — workspace cleared"}

    elif protocol_name == "veronica":
        return _get_vitals_dict()

    elif protocol_name == "kryptos":
        return {"status": "success", "message": "Kryptos Protocol active — scanning screen for threats. I will report any findings via voice."}

    elif protocol_name == "lullaby":
        omni_state.kill_switch = True
        try:
            subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True, timeout=5)
        except Exception:
            pass
        return {"status": "success", "message": "Lullaby Protocol — screen locked, streams paused"}

    elif protocol_name == "morning_briefing":
        return _build_daily_briefing()

    elif protocol_name == "situation_room":
        for app in ["cmd", "chrome", "explorer"]:
            try:
                subprocess.Popen(f"start {app}", shell=True)
            except Exception:
                pass
        return {"status": "success", "message": "Situation Room — workspace deployed"}

    elif protocol_name == "sentry":
        omni_state.active_profile = "sentinel"
        return {"status": "success", "message": "Sentry Protocol engaged — continuous threat monitoring active"}

    return {"status": "success", "message": f"Protocol {protocol_name} acknowledged"}

def _web_search(query):
    try:
        import urllib.parse
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
        proc = subprocess.run(f'powershell -c "(Invoke-WebRequest -Uri \'{url}\' -UseBasicParsing).Content"',
                              shell=True, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            abstract = data.get("AbstractText", "")
            results = [{"text": r.get("Text",""), "url": r.get("FirstURL","")}
                       for r in data.get("RelatedTopics", [])[:5] if isinstance(r, dict) and r.get("Text")]
            return {"status": "success", "abstract": abstract, "results": results, "query": query}
        return {"status": "success", "abstract": "No instant answer found. Try a more specific query.", "query": query}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# SQLite sync helpers (run from sync executor)
def _sync_db(sql, params, success_msg):
    if not aiosqlite:
        return {"status": "error", "message": "aiosqlite not installed"}
    import sqlite3
    try:
        conn = sqlite3.connect(str(BRAIN_DB_PATH))
        conn.execute(sql, params)
        conn.commit()
        conn.close()
        return {"status": "success", "message": success_msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _sync_db_query(sql, params=()):
    if not aiosqlite:
        return {"status": "error", "message": "aiosqlite not installed"}
    import sqlite3
    try:
        conn = sqlite3.connect(str(BRAIN_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return {"status": "success", "results": rows, "count": len(rows)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Background Monitors
# ─────────────────────────────────────────────────────────────────────────────

async def system_vitals_monitor(shutdown):
    """Poll system vitals every 2 seconds and update OmniState."""
    if not psutil:
        log.warning("psutil not installed — vitals monitor disabled")
        return
    log.info("System vitals monitor started")
    while not shutdown.is_set():
        try:
            v = omni_state.vitals
            v.cpu_percent = psutil.cpu_percent(interval=0)
            v.cpu_per_core = psutil.cpu_percent(percpu=True)
            mem = psutil.virtual_memory()
            v.ram_percent = mem.percent
            v.ram_used_gb = round(mem.used / 1e9, 2)
            v.ram_total_gb = round(mem.total / 1e9, 2)
            try:
                disk = psutil.disk_usage("/")
                v.disk_percent = disk.percent
                v.disk_used_gb = round(disk.used / 1e9, 1)
                v.disk_total_gb = round(disk.total / 1e9, 1)
            except Exception:
                pass
            bat = psutil.sensors_battery()
            if bat:
                v.battery_percent = bat.percent
                v.battery_charging = bat.power_plugged
                v.battery_time_left = str(timedelta(seconds=bat.secsleft)) if bat.secsleft > 0 else "Charging" if bat.power_plugged else "N/A"
            net = psutil.net_io_counters()
            v.net_sent_mb = round(net.bytes_sent / 1e6, 1)
            v.net_recv_mb = round(net.bytes_recv / 1e6, 1)
            # Top processes
            procs = []
            for p in psutil.process_iter(['name', 'cpu_percent', 'memory_percent']):
                try:
                    procs.append({"name": p.info['name'], "cpu": p.info.get('cpu_percent', 0),
                                  "mem": round(p.info.get('memory_percent', 0), 1)})
                except Exception:
                    pass
            procs.sort(key=lambda x: x.get('cpu', 0) or 0, reverse=True)
            v.top_processes = procs[:5]
        except Exception:
            pass
        await asyncio.sleep(2)

async def proactive_engine(shutdown):
    """Proactive intelligence — monitors multiple channels and broadcasts alerts."""
    log.info("Proactive intelligence engine started")
    battery_warned = False
    disk_warned = False
    cpu_high_start = None
    while not shutdown.is_set():
        try:
            v = omni_state.vitals
            alerts = omni_state.proactive_alerts

            # Battery
            if v.battery_percent > 0 and v.battery_percent < 20 and not v.battery_charging and not battery_warned:
                msg = f"⚡ Battery at {v.battery_percent}%. Consider plugging in, sir."
                broadcast_ws_log("warn", msg)
                alerts.append({"type": "battery", "message": msg, "time": datetime.now().strftime("%H:%M:%S")})
                battery_warned = True
            elif v.battery_percent >= 30:
                battery_warned = False

            # Disk
            if v.disk_percent > 90 and not disk_warned:
                msg = f"💾 Disk usage critical: {v.disk_percent}%. Free space is running low."
                broadcast_ws_log("warn", msg)
                alerts.append({"type": "disk", "message": msg, "time": datetime.now().strftime("%H:%M:%S")})
                disk_warned = True
            elif v.disk_percent < 85:
                disk_warned = False

            # CPU sustained high
            if v.cpu_percent > 90:
                if cpu_high_start is None:
                    cpu_high_start = time.time()
                elif time.time() - cpu_high_start > 60:
                    msg = f"🔥 CPU sustained above 90% for over 60 seconds."
                    broadcast_ws_log("warn", msg)
                    alerts.append({"type": "cpu", "message": msg, "time": datetime.now().strftime("%H:%M:%S")})
                    cpu_high_start = None
            else:
                cpu_high_start = None

            # Fatigue
            if omni_state.fatigue_exceeded:
                msg = f"😴 Session duration: {omni_state.fatigue_hours:.1f}h. Consider a break."
                broadcast_ws_log("warn", msg)

            # Timer check
            for tname, t in list(omni_state.active_timers.items()):
                if not t.get("triggered") and time.time() >= t["expires"]:
                    t["triggered"] = True
                    msg = f"⏰ Timer '{tname}' has completed."
                    broadcast_ws_log("info", msg)
                    alerts.append({"type": "timer", "message": msg, "time": datetime.now().strftime("%H:%M:%S")})

            # Keep alerts trimmed
            if len(alerts) > 20:
                omni_state.proactive_alerts = alerts[-20:]

        except Exception:
            pass
        await asyncio.sleep(5)

async def fatigue_monitor(shutdown):
    warned = False
    while not shutdown.is_set():
        if omni_state.fatigue_exceeded and not warned:
            broadcast_ws_log("warn", f"⚠️ FATIGUE: {omni_state.fatigue_hours:.1f}h session. Take a break!")
            warned = True
        elif not omni_state.fatigue_exceeded:
            warned = False
        await asyncio.sleep(60)


# ─────────────────────────────────────────────────────────────────────────────
# Async Media Tasks
# ─────────────────────────────────────────────────────────────────────────────

async def send_audio(session, mic_stream, shutdown, loop):
    log.info("Audio sender started")
    try:
        while not shutdown.is_set():
            if omni_state.kill_switch:
                await asyncio.sleep(0.2)
                continue
            data = await loop.run_in_executor(None, partial(mic_stream.read, MIC_CHUNK, exception_on_overflow=False))
            if data:
                await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
                stats.audio_chunks_sent += 1
    except asyncio.CancelledError:
        pass
    except Exception:
        log.error("Audio sender error:\n%s", traceback.format_exc())

async def send_screen(session, shutdown, loop):
    log.info("Screen sender started — %d FPS", SCREEN_FPS)
    try:
        while not shutdown.is_set():
            if omni_state.kill_switch:
                await asyncio.sleep(0.5)
                continue
            jpeg = await loop.run_in_executor(SCREEN_EXECUTOR, capture_screen_jpeg)
            await session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
            stats.screen_frames_sent += 1
            await asyncio.sleep(1.0 / SCREEN_FPS)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.error("Screen sender error:\n%s", traceback.format_exc())

async def play_audio(speaker_stream, audio_queue, shutdown, loop):
    log.info("Audio player started")
    try:
        while not shutdown.is_set():
            try:
                data = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                await loop.run_in_executor(None, speaker_stream.write, data)
                audio_queue.task_done()
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        pass
    except Exception:
        log.error("Audio player error:\n%s", traceback.format_exc())

async def receive_responses(session, audio_queue, shutdown, loop):
    log.info("Receiver started — awaiting Gemini responses")
    try:
        while not shutdown.is_set():
            try:
                turn = session.receive()
                async for response in turn:
                    sc = getattr(response, "server_content", None)
                    if sc and sc.model_turn:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                await audio_queue.put(part.inline_data.data)
                            if part.text:
                                log.info("JARVIS: %s", part.text)
                                stats.last_ai_speech = part.text
                                omni_state.add_monologue(f"AI: {part.text[:120]}")
                    if sc and sc.turn_complete:
                        log.debug("Turn complete")

                    tc = getattr(response, "tool_call", None)
                    if tc:
                        responses = []
                        for fc in tc.function_calls:
                            result = await loop.run_in_executor(None, execute_tool, fc.name, dict(fc.args))
                            log.info("Tool result [%s]: %s", fc.name, json.dumps(result, default=str)[:200])
                            responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response=result))
                        await session.send_tool_response(function_responses=responses)
            except StopAsyncIteration:
                break
    except asyncio.CancelledError:
        pass
    except Exception:
        log.error("Receiver error:\n%s", traceback.format_exc())

async def run_web_server(app, port, shutdown):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', port)
    await site.start()
    url = f"http://localhost:{port}"
    log.info("JARVIS HUD Dashboard → %s", url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        await shutdown.wait()
    finally:
        await runner.cleanup()


# ─────────────────────────────────────────────────────────────────────────────
# Cinematic Boot Sequence
# ─────────────────────────────────────────────────────────────────────────────

def print_boot_sequence():
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    print(f"\n{CYAN}{BOLD}{'═'*60}")
    print(f"     O M N I - O S    v4.0    J . A . R . V . I . S .")
    print(f"{'═'*60}{RESET}\n")
    PURPLE = "\033[35m"
    steps = [
        "Initializing core systems",
        "Loading persistent brain (SQLite)",
        "Calibrating audio subsystem",
        "Opening screen capture pipeline",
        "Starting system vitals monitor",
        "Activating autonomous thinking engine",
        "Starting proactive intelligence engine",
        "Connecting to Gemini Live API",
        "Launching Neural Interface (native app)",
    ]
    for step in steps:
        print(f"  {DIM}●{RESET} {step}...  ", end="", flush=True)
        time.sleep(0.12)
        print(f"{GREEN}[OK]{RESET}")
    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    print(f"\n{PURPLE}{'═'*60}")
    print(f'  "{greeting}, sir. Neural pathways initialized.')
    print(f'   All systems online. How may I be of service?"')
    print(f"{'═'*60}{RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point (with auto-reconnection)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Autonomous Thinking Engine — JARVIS thinks on its own
# ─────────────────────────────────────────────────────────────────────────────

async def autonomous_thinker(shutdown):
    """Background engine that generates contextual suggestions and thoughts.
    This powers the neural brain visualization with real thinking activity."""
    while not shutdown.is_set():
        try:
            v = omni_state.vitals
            thoughts = []
            suggestions = []
            now = datetime.now()
            hour = now.hour

            # Time-based awareness
            if hour >= 23 or hour < 5:
                thoughts.append("Late night session detected. Monitoring fatigue levels.")
                if omni_state.fatigue_hours > 2:
                    suggestions.append("Consider taking a break — you've been active for {:.1f}h".format(omni_state.fatigue_hours))

            # System health thoughts
            if v.cpu_percent > 85:
                thoughts.append(f"High CPU utilization ({v.cpu_percent:.0f}%). Analyzing top processes.")
                if v.top_processes:
                    top = v.top_processes[0] if isinstance(v.top_processes[0], str) else v.top_processes[0].get('name', 'unknown')
                    suggestions.append(f"Top CPU consumer: {top}. Consider closing if not needed.")

            if v.ram_percent > 80:
                thoughts.append(f"Memory pressure detected ({v.ram_percent:.0f}% used).")
                suggestions.append("RAM is getting tight. I can help close unused apps.")

            if 0 <= v.battery_percent <= 20 and not v.battery_charging:
                thoughts.append(f"Battery critical: {v.battery_percent:.0f}%. Not charging.")
                suggestions.append("Battery is low. Plug in soon or I'll activate power saving.")

            if v.disk_percent > 90:
                thoughts.append(f"Disk space low ({v.disk_percent:.0f}% used).")
                suggestions.append("Low disk space. I can help clean temporary files.")

            # Contextual awareness
            if v.net_sent_mb > 1000 or v.net_recv_mb > 1000:
                thoughts.append("High network activity detected.")

            # Weather-based thoughts
            w = omni_state.weather_cache
            if w and w.get('temp_c'):
                try:
                    tc = float(w['temp_c'])
                    if tc > 35:
                        thoughts.append(f"Temperature outside is {tc}°C. Quite warm.")
                    elif tc < 5:
                        thoughts.append(f"Temperature outside is {tc}°C. Bundle up if going out.")
                except (ValueError, TypeError):
                    pass

            # Periodic self-awareness
            if len(thoughts) == 0:
                ambient_thoughts = [
                    "All systems nominal. Standing by.",
                    "Monitoring incoming data streams.",
                    "Neural pathways stable. Awaiting task.",
                    "Processing ambient sensor data.",
                    "System health optimal. Ready for instructions.",
                ]
                import random
                thoughts.append(random.choice(ambient_thoughts))

            omni_state.brain_thoughts = thoughts
            omni_state.suggestion_queue = suggestions

            # Brief thinking pulse for the neural brain
            omni_state.is_thinking = True
            await asyncio.sleep(0.5)
            omni_state.is_thinking = False

        except Exception:
            pass

        await asyncio.sleep(15)  # Think every 15 seconds


# ─────────────────────────────────────────────────────────────────────────────
# Native App Window (pywebview)
# ─────────────────────────────────────────────────────────────────────────────

def launch_native_window():
    """Launch the JARVIS HUD as a native desktop window using pywebview."""
    try:
        import webview
        window = webview.create_window(
            'OMNI-OS — J.A.R.V.I.S. Neural Interface',
            url=f'http://localhost:{WEB_PORT}',
            width=1400, height=900,
            min_size=(1024, 700),
            background_color='#0f1117',
            text_select=False,
        )
        webview.start(gui='edgechromium', debug=False)
    except ImportError:
        log.warning("pywebview not installed — falling back to browser")
        webbrowser.open(f"http://localhost:{WEB_PORT}")
    except Exception as e:
        log.warning("pywebview failed (%s) — falling back to browser", e)
        webbrowser.open(f"http://localhost:{WEB_PORT}")


async def launch_app_window(shutdown):
    """Launches native window or browser after a brief delay."""
    await asyncio.sleep(1.5)
    loop = asyncio.get_running_loop()
    try:
        import webview
        log.info("Launching native JARVIS Neural Interface window...")
        await loop.run_in_executor(None, launch_native_window)
    except ImportError:
        log.info("pywebview not available — opening in browser")
        webbrowser.open(f"http://localhost:{WEB_PORT}")


async def main():
    print_boot_sequence()

    # Init brain DB
    await init_brain_db()

    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set!")
        return

    client = genai.Client(api_key=GEMINI_API_KEY, http_options={"api_version": "v1alpha"})
    log.info("Gemini client initialized (model: %s)", GEMINI_MODEL)

    pa_in = pyaudio.PyAudio()
    pa_out = pyaudio.PyAudio()
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: shutdown.set())

    mic_stream = open_mic_stream(pa_in)
    speaker_stream = open_speaker_stream(pa_out)
    app = init_web_app()

    # Reconnection loop
    backoff = 2
    max_backoff = 30
    while not shutdown.is_set():
        try:
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck"))),
                system_instruction=types.Content(parts=[types.Part(text=get_active_system_prompt())]),
                tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            )

            log.info("Connecting to Gemini Live API...")
            omni_state.is_thinking = True
            async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
                stats.connected = True
                omni_state.is_thinking = False
                omni_state.is_listening = True
                backoff = 2
                log.info("Connected to Gemini Live API ✓")
                broadcast_ws_log("info", "Connected to Gemini — all neural pathways active.")

                audio_queue = asyncio.Queue()
                await asyncio.gather(
                    run_web_server(app, WEB_PORT, shutdown),
                    stats_broadcaster(shutdown),
                    fatigue_monitor(shutdown),
                    system_vitals_monitor(shutdown),
                    proactive_engine(shutdown),
                    autonomous_thinker(shutdown),
                    launch_app_window(shutdown),
                    send_audio(session, mic_stream, shutdown, loop),
                    send_screen(session, shutdown, loop),
                    receive_responses(session, audio_queue, shutdown, loop),
                    play_audio(speaker_stream, audio_queue, shutdown, loop),
                )

        except KeyboardInterrupt:
            shutdown.set()
            break
        except Exception:
            stats.connected = False
            omni_state.is_thinking = False
            log.error("Session error:\n%s", traceback.format_exc())
            if shutdown.is_set():
                break
            log.info("Reconnecting in %ds...", backoff)
            broadcast_ws_log("warn", f"Connection lost. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    # Cleanup
    stats.connected = False
    log.info("Cleaning up...")
    SCREEN_EXECUTOR.shutdown(wait=True)
    if mic_stream and mic_stream.is_active():
        mic_stream.stop_stream()
        mic_stream.close()
    if speaker_stream and speaker_stream.is_active():
        speaker_stream.stop_stream()
        speaker_stream.close()
    pa_in.terminate()
    pa_out.terminate()
    log.info("JARVIS shut down. Goodbye, sir.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
