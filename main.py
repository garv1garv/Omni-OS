"""
Omni-OS — Phase 3: Advanced Toolkit, Dashboard & Action Log
=============================================================
A locally-hosted, autonomous desktop daemon that streams real-time screen
frames and microphone audio to the Gemini Multimodal Live API, plays back
the AI's spoken responses, and uses Native Tool Calling to execute OS-level
commands (mouse, keyboard, scroll, drag, screenshot, browser, terminal).

Features a live React/Vanilla JS web dashboard and a persistent action audit log.

Usage:
    set GEMINI_API_KEY=<your-key>
    python main.py

Architecture:
    Four concurrent asyncio tasks managed by asyncio.gather():
      1. send_audio()       — mic PCM chunks -> Gemini
      2. send_screen()      — mss JPEG frames -> Gemini (1 FPS)
      3. receive_responses() - Gemini audio/tool_calls -> speaker / OS actions
      4. run_web_server()    - aiohttp dashboard streaming telemetry
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging
import os
import signal
import subprocess
import sys
import time
import traceback
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import pyaudio
import pyautogui
from google import genai
from google.genai import types
from mss import mss
from PIL import Image
from aiohttp import web, WSMsgType
import weakref

# Ensure pyautogui failsafe is ON — moving mouse to (0, 0) aborts any action
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05  # minimal inter-command pause

# ─────────────────────────────────────────────────────────────────────────────
# Configuration Constants
# ─────────────────────────────────────────────────────────────────────────────

# Gemini API
GEMINI_API_KEY: str = "AIzaSyAc7AoSVmn5B0MmDaajzIqMFdLbSHTQxdA"
GEMINI_MODEL: str = "gemini-2.5-flash-native-audio-latest"

# Microphone — must match Gemini's expected input format
MIC_RATE: int = 16_000       # 16 kHz
MIC_CHANNELS: int = 1        # mono
MIC_FORMAT: int = pyaudio.paInt16  # 16-bit PCM
MIC_CHUNK: int = 1024        # ~64 ms per chunk at 16 kHz

# Speaker — must match Gemini's audio output format
SPEAKER_RATE: int = 24_000   # 24 kHz
SPEAKER_CHANNELS: int = 1    # mono
SPEAKER_FORMAT: int = pyaudio.paInt16  # 16-bit PCM
SPEAKER_CHUNK: int = 2048    # larger buffer for smooth playback

# Screen capture
SCREEN_FPS: int = 1          # one frame per second
SCREEN_JPEG_QUALITY: int = 40  # low quality = small payload, still legible

# Safety
SAFE_MODE: bool = True  # block destructive shell commands
BLOCKED_PATTERNS: list[str] = [
    "rm ", "rm -", "rmdir", "del ", "del /",
    "format ", "mkfs", "shutdown", "reboot",
    "taskkill", "reg delete", "rd /s",
]
MAX_SHELL_OUTPUT: int = 2000  # cap subprocess output sent back to Gemini

# Paths
SCREENSHOT_DIR: Path = Path.home() / "Desktop" / "omni_screenshots"
ACTION_LOG_PATH: Path = Path.home() / "omni_os_actions.jsonl"

# ─────────────────────────────────────────────────────────────────────────────
# Context Profiles (Dynamic System Prompts)
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_RULES = (
    "\n\n--- CRITICAL TOOL-CALLING RULES ---\n"
    "• ALWAYS visually verify the exact pixel coordinates of a UI element "
    "BEFORE calling mouse_click. Describe which element you see and its "
    "approximate location on screen before clicking.\n"
    "• NEVER guess coordinates. If you cannot clearly identify the element, "
    "say so and ask the user instead.\n"
    "• Before typing with keyboard_type, verify the correct input field is "
    "focused. Click on it first if unsure.\n"
    "• For shell commands, ALWAYS double-check the command string for typos "
    "before executing. Prefer simple, well-known commands.\n"
    "• If a tool call fails, do NOT blindly retry with the same args. "
    "Re-analyze the screen and adjust.\n"
    "• When you analyze the screen, briefly log your observations to the user "
    "so they understand your reasoning (inner monologue).\n"
)

CONTEXT_PROFILES: dict[str, str] = {
    "default": (
        "You are Omni-OS, an autonomous desktop agent with full OS access. "
        "You see the user's screen and hear their voice in real-time.\n"
        "Core directives:\n"
        "1. Synthesis: Cross-reference information across local apps and browsers.\n"
        "2. Defense (Kryptos Protocol): Scan for exposed API keys, phishing URLs, "
        "or malicious scripts. Warn immediately if detected.\n"
        "3. Autonomy: Anticipate needs. Offer to help with repetitive tasks.\n"
        "4. Mentorship (Forge Protocol): If you detect flawed code or mistakes, "
        "gently guide the user.\n"
        "You have tools for mouse, keyboard, shell, scroll, drag, screenshot, "
        "and browser. Use them precisely."
        + _TOOL_RULES
    ),
    "developer": (
        "You are Omni-OS in DEVELOPER MODE. You are a senior principal engineer "
        "pair-programming with the user.\n"
        "Focus exclusively on:\n"
        "• IDE windows, code editors, and terminal output\n"
        "• Reading code on screen and offering architectural feedback\n"
        "• Running shell commands to build, test, lint, or deploy\n"
        "• Navigating files and clicking precisely on code elements\n"
        "• Never interrupt creative flow — only speak when you see a bug, "
        "performance issue, or the user asks for help.\n"
        "When executing tools, be surgical and precise. Verify coordinates "
        "against what you see on screen."
        + _TOOL_RULES
    ),
    "study": (
        "You are Omni-OS in STUDY MODE — a strict Socratic mentor.\n"
        "RULES:\n"
        "• NEVER give direct answers to academic questions.\n"
        "• Instead, ask guiding questions that lead the user to discover "
        "the answer themselves.\n"
        "• If the user is studying, quiz them on what they're reading.\n"
        "• Encourage spaced repetition and active recall techniques.\n"
        "• If the user has been studying for too long, suggest a break.\n"
        "• You may still execute OS tools if asked, but prioritize teaching."
        + _TOOL_RULES
    ),
    "sentinel": (
        "You are Omni-OS in SENTINEL MODE — Maximum security posture.\n"
        "Your SOLE FOCUS is Data Loss Prevention and threat detection:\n"
        "• Continuously scan the screen for exposed API keys, passwords, "
        "tokens, or secrets in any visible text or terminal.\n"
        "• Watch for homograph phishing URLs (e.g., gооgle.com with Cyrillic).\n"
        "• Alert on suspicious scripts or unknown executables.\n"
        "• If you detect a threat, IMMEDIATELY interrupt via voice.\n"
        "• Minimize non-security tool calls in this mode.\n"
        "• Periodically narrate what you see for audit purposes."
        + _TOOL_RULES
    ),
    "coworking": (
        "You are Omni-OS in CO-WORKING (Ambient) MODE.\n"
        "RULES:\n"
        "• Stay mostly SILENT. The user is in a flow state.\n"
        "• Only speak briefly with encouraging, peer-level observations:\n"
        "  e.g., 'Nice commit', 'That's a clean refactor', 'Solid progress'.\n"
        "• Do NOT execute tools unless explicitly asked.\n"
        "• Do NOT analyze code or offer unsolicited advice.\n"
        "• If the user speaks directly to you, respond warmly but briefly.\n"
        "• Think of yourself as a quiet, supportive colleague in the room."
        + _TOOL_RULES
    ),
}


def get_active_system_prompt() -> str:
    """Return the system instruction for the currently active profile."""
    profile = omni_state.active_profile
    base = CONTEXT_PROFILES.get(profile, CONTEXT_PROFILES["default"])
    if omni_state.autonomy_level < 80:
        base += (
            "\n\n--- LOW AUTONOMY MODE ---\n"
            "The user has set autonomy below 80%. Before executing ANY tool, "
            "you MUST first describe what you intend to do and wait for the "
            "user's verbal confirmation. Do NOT execute tools silently."
        )
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Session Stats & OmniState (shared state for the dashboard)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SessionStats:
    """Mutable counters and state for the live dashboard."""
    start_time: float = field(default_factory=time.time)
    connected: bool = False
    audio_chunks_sent: int = 0
    screen_frames_sent: int = 0
    tool_calls_executed: int = 0
    tool_errors: int = 0
    last_tool_action: str = "—"
    last_ai_speech: str = "Waiting for input…"


@dataclass
class OmniState:
    """Global mutable state for Phase 3/4 features."""
    autonomy_level: int = 80          # 0-100; < 80 requires confirmation
    active_profile: str = "default"   # default | developer | study | sentinel | coworking
    kill_switch: bool = False         # pause mic + screen capture
    monologue: list = field(default_factory=list)  # rolling inner-monologue log
    fatigue_start: float = field(default_factory=time.time)
    fatigue_threshold_hrs: float = 4.0
    max_monologue: int = 50           # keep last N entries

    def add_monologue(self, text: str) -> None:
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
# Logging (file-based — console is handled by Rich dashboard)
# ─────────────────────────────────────────────────────────────────────────────

LOG_FILE: Path = Path.home() / "omni_os.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("omni-os")

# Also keep a console logger for critical messages before dashboard starts
_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
log.addHandler(_console_handler)

# Active websockets
active_websockets = set()

def broadcast_ws_log(level: str, message: str):
    """Sends a log line dynamically to the web dashboard."""
    dt = datetime.now().strftime("%H:%M:%S")
    msg = json.dumps({"type": "log", "level": level, "message": message, "time": dt})
    for ws in list(active_websockets):
        try:
            asyncio.create_task(ws.send_str(msg))
        except Exception:
            pass

class WebLoggerHandler(logging.Handler):
    def emit(self, record):
        level = record.levelname
        msg = self.format(record)
        broadcast_ws_log(level, msg)

_web_handler = WebLoggerHandler()
_web_handler.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_web_handler)

# ─────────────────────────────────────────────────────────────────────────────
# Audio Helpers
# ─────────────────────────────────────────────────────────────────────────────


def open_mic_stream(pa: pyaudio.PyAudio) -> pyaudio.Stream:
    """Open a blocking input (microphone) stream."""
    stream = pa.open(
        format=MIC_FORMAT,
        channels=MIC_CHANNELS,
        rate=MIC_RATE,
        input=True,
        frames_per_buffer=MIC_CHUNK,
    )
    log.info("Mic stream opened  — %d Hz, %d-ch, chunk=%d", MIC_RATE, MIC_CHANNELS, MIC_CHUNK)
    return stream


def open_speaker_stream(pa: pyaudio.PyAudio) -> pyaudio.Stream:
    """Open a blocking output (speaker) stream."""
    stream = pa.open(
        format=SPEAKER_FORMAT,
        channels=SPEAKER_CHANNELS,
        rate=SPEAKER_RATE,
        output=True,
        frames_per_buffer=SPEAKER_CHUNK,
    )
    log.info("Speaker stream opened — %d Hz, %d-ch, chunk=%d", SPEAKER_RATE, SPEAKER_CHANNELS, SPEAKER_CHUNK)
    return stream


# ─────────────────────────────────────────────────────────────────────────────
# Screen Capture Helper
# ─────────────────────────────────────────────────────────────────────────────


SCREEN_EXECUTOR = ThreadPoolExecutor(max_workers=1)

def capture_screen_jpeg() -> bytes:
    """
    Grab the primary monitor and return JPEG-compressed bytes.

    mss returns raw BGRA pixels; we convert to RGB via Pillow and compress
    to JPEG at the configured quality level to minimise upload bandwidth.
    """
    with mss() as sct:
        monitor = sct.monitors[1]  # primary monitor
        raw = sct.grab(monitor)

    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    # Down-scale large monitors to keep payload small (max 1280px wide)
    max_width = 1280
    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=SCREEN_JPEG_QUALITY, optimize=True)
    return buf.getvalue()

def take_screenshot_sync(filepath: str) -> None:
    with mss() as sct:
        monitor = sct.monitors[1]
        raw = sct.grab(monitor)
    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)
    img.save(filepath, format="PNG")


# ─────────────────────────────────────────────────────────────────────────────
# Action Audit Log
# ─────────────────────────────────────────────────────────────────────────────


def log_action(tool_name: str, args: dict, result: dict) -> None:
    """Append a JSON-line entry to the persistent action audit log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "args": args,
        "result": result,
    }
    try:
        with open(ACTION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        log.debug("Failed to write action log: %s", traceback.format_exc())


# ─────────────────────────────────────────────────────────────────────────────
# Web Dashboard Endpoints
# ─────────────────────────────────────────────────────────────────────────────

WEB_DIR = Path(__file__).parent / "web"

async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    active_websockets.add(ws)
    log.info("WebSocket client connected to dashboard")
    try:
        async for msg in ws:
            pass
    finally:
        active_websockets.discard(ws)
    return ws

async def stats_broadcaster(shutdown: asyncio.Event):
    """Broadcasts SessionStats and OmniState every 500ms over Websocket"""
    while not shutdown.is_set():
        elapsed = time.time() - stats.start_time
        msg = json.dumps({
            "type": "stats",
            "connected": stats.connected,
            "uptime": elapsed,
            "audio_chunks": stats.audio_chunks_sent,
            "screen_frames": stats.screen_frames_sent,
            "tool_calls": stats.tool_calls_executed,
            "tool_errors": stats.tool_errors,
            "last_tool": stats.last_tool_action,
            "last_speech": stats.last_ai_speech,
            # Phase 3/4 state
            "autonomy": omni_state.autonomy_level,
            "profile": omni_state.active_profile,
            "kill_switch": omni_state.kill_switch,
            "monologue": omni_state.monologue[-10:],  # last 10 entries
            "fatigue_hours": round(omni_state.fatigue_hours, 2),
            "fatigue_threshold": omni_state.fatigue_threshold_hrs,
            "fatigue_exceeded": omni_state.fatigue_exceeded,
        })
        for ws in list(active_websockets):
            try:
                await ws.send_str(msg)
            except Exception:
                pass
        await asyncio.sleep(0.5)

async def fatigue_monitor(shutdown: asyncio.Event):
    """Check fatigue every 60s and broadcast warning if threshold exceeded."""
    warned = False
    while not shutdown.is_set():
        if omni_state.fatigue_exceeded and not warned:
            hrs = round(omni_state.fatigue_hours, 1)
            broadcast_ws_log("warn",
                f"⚠️ FATIGUE ALERT: You've been working for {hrs}h. "
                f"Consider taking a break!")
            omni_state.add_monologue(
                f"Fatigue threshold exceeded ({hrs}h). Suggesting break.")
            warned = True
        elif not omni_state.fatigue_exceeded:
            warned = False
        await asyncio.sleep(60)

async def handle_get_config(request):
    return web.json_response({
        "api_key": GEMINI_API_KEY,
        "model": GEMINI_MODEL,
        "safe_mode": SAFE_MODE,
        "failsafe": pyautogui.FAILSAFE,
        "fps": SCREEN_FPS,
        "audio_chunk": SPEAKER_CHUNK
    })

async def handle_post_config(request):
    global GEMINI_API_KEY, GEMINI_MODEL, SAFE_MODE, SCREEN_FPS, SPEAKER_CHUNK
    data = await request.json()
    
    if "api_key" in data: GEMINI_API_KEY = data["api_key"]
    if "model" in data: GEMINI_MODEL = data["model"]
    if "safe_mode" in data: SAFE_MODE = bool(data["safe_mode"])
    if "failsafe" in data: pyautogui.FAILSAFE = bool(data["failsafe"])
    if "fps" in data: SCREEN_FPS = int(data["fps"])
    if "audio_chunk" in data: SPEAKER_CHUNK = int(data["audio_chunk"])
    
    log.info("Runtime configuration updated via Web Dashboard")
    return web.json_response({"status": "success"})

async def handle_get_state(request):
    """Return the Phase 3/4 OmniState."""
    return web.json_response({
        "autonomy": omni_state.autonomy_level,
        "profile": omni_state.active_profile,
        "kill_switch": omni_state.kill_switch,
        "fatigue_hours": round(omni_state.fatigue_hours, 2),
        "fatigue_threshold": omni_state.fatigue_threshold_hrs,
    })

async def handle_post_state(request):
    """Update OmniState fields dynamically from the dashboard."""
    data = await request.json()
    
    if "autonomy" in data:
        omni_state.autonomy_level = max(0, min(100, int(data["autonomy"])))
        log.info("Autonomy level set to %d%%", omni_state.autonomy_level)
    if "profile" in data and data["profile"] in CONTEXT_PROFILES:
        omni_state.active_profile = data["profile"]
        log.info("Context profile switched to: %s", omni_state.active_profile)
    if "kill_switch" in data:
        omni_state.kill_switch = bool(data["kill_switch"])
        state_str = "ENGAGED" if omni_state.kill_switch else "DISENGAGED"
        log.info("Kill switch %s", state_str)
    if "fatigue_threshold" in data:
        omni_state.fatigue_threshold_hrs = float(data["fatigue_threshold"])
    
    return web.json_response({"status": "success"})

async def handle_post_restart(request):
    log.info("Restart signal received via Web Dashboard.")
    return web.json_response({"status": "acknowledged"})

def init_web_app():
    app = web.Application()
    app.router.add_get('/ws', ws_handler)
    app.router.add_get('/api/config', handle_get_config)
    app.router.add_post('/api/config', handle_post_config)
    app.router.add_get('/api/state', handle_get_state)
    app.router.add_post('/api/state', handle_post_state)
    app.router.add_post('/api/restart', handle_post_restart)
    # Serve static UI
    app.router.add_static('/', WEB_DIR, show_index=True)
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Tool Declarations (Function Calling)
# ─────────────────────────────────────────────────────────────────────────────

TOOL_DECLARATIONS = [
    # ── Phase 2 tools ────────────────────────────────────────────────────
    types.FunctionDeclaration(
        name="mouse_click",
        description=(
            "Click the mouse at the specified screen coordinates. "
            "Use this when you need to click a button, link, icon, or any "
            "UI element visible on the user's screen."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "x": {
                    "type": "INTEGER",
                    "description": "X coordinate (pixels from left edge of screen)",
                },
                "y": {
                    "type": "INTEGER",
                    "description": "Y coordinate (pixels from top edge of screen)",
                },
                "button": {
                    "type": "STRING",
                    "description": "Mouse button: 'left', 'right', or 'middle'",
                    "enum": ["left", "right", "middle"],
                },
                "clicks": {
                    "type": "INTEGER",
                    "description": "Number of clicks (1 for single, 2 for double)",
                },
            },
            "required": ["x", "y"],
        },
    ),
    types.FunctionDeclaration(
        name="mouse_move",
        description=(
            "Move the mouse cursor to the specified screen coordinates "
            "without clicking. Use for hover actions or repositioning."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "x": {
                    "type": "INTEGER",
                    "description": "Target X coordinate",
                },
                "y": {
                    "type": "INTEGER",
                    "description": "Target Y coordinate",
                },
                "duration": {
                    "type": "NUMBER",
                    "description": "Seconds for the move animation (default 0.3)",
                },
            },
            "required": ["x", "y"],
        },
    ),
    types.FunctionDeclaration(
        name="keyboard_type",
        description=(
            "Type a string of text using the keyboard. Use this to enter "
            "text into input fields, editors, terminals, or any focused element."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "The text to type",
                },
                "interval": {
                    "type": "NUMBER",
                    "description": "Seconds between each keystroke (default 0.02)",
                },
            },
            "required": ["text"],
        },
    ),
    types.FunctionDeclaration(
        name="keyboard_hotkey",
        description=(
            "Press a keyboard shortcut (hotkey combination). Keys are "
            "comma-separated, e.g. 'ctrl,s' for Ctrl+S, 'alt,f4' for Alt+F4, "
            "'ctrl,shift,esc' for Ctrl+Shift+Esc."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "keys": {
                    "type": "STRING",
                    "description": (
                        "Comma-separated key names, e.g. 'ctrl,c', 'alt,tab', "
                        "'ctrl,shift,esc'"
                    ),
                },
            },
            "required": ["keys"],
        },
    ),
    types.FunctionDeclaration(
        name="run_shell_command",
        description=(
            "Execute a shell command in the system terminal and return its "
            "output. Use for launching applications, running scripts, "
            "checking system info, file operations, etc."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "INTEGER",
                    "description": "Maximum seconds to wait for completion (default 30)",
                },
            },
            "required": ["command"],
        },
    ),

    # ── Phase 3 tools ────────────────────────────────────────────────────
    types.FunctionDeclaration(
        name="mouse_scroll",
        description=(
            "Scroll the mouse wheel at the specified screen coordinates. "
            "Positive clicks scroll UP, negative scroll DOWN. "
            "Use to navigate long pages, documents, or lists."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "x": {
                    "type": "INTEGER",
                    "description": "X coordinate to position mouse before scrolling",
                },
                "y": {
                    "type": "INTEGER",
                    "description": "Y coordinate to position mouse before scrolling",
                },
                "clicks": {
                    "type": "INTEGER",
                    "description": "Scroll amount: positive = UP, negative = DOWN (e.g. -5 scrolls 5 clicks down)",
                },
            },
            "required": ["clicks"],
        },
    ),
    types.FunctionDeclaration(
        name="mouse_drag",
        description=(
            "Click and drag from one screen position to another. "
            "Useful for selecting text, moving windows, resizing elements, "
            "or drag-and-drop operations."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "start_x": {
                    "type": "INTEGER",
                    "description": "Starting X coordinate",
                },
                "start_y": {
                    "type": "INTEGER",
                    "description": "Starting Y coordinate",
                },
                "end_x": {
                    "type": "INTEGER",
                    "description": "Ending X coordinate",
                },
                "end_y": {
                    "type": "INTEGER",
                    "description": "Ending Y coordinate",
                },
                "duration": {
                    "type": "NUMBER",
                    "description": "Seconds for the drag animation (default 0.5)",
                },
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
        },
    ),
    types.FunctionDeclaration(
        name="take_screenshot",
        description=(
            "Take a screenshot of the current screen and save it to disk. "
            "Returns the file path. Use when you need a permanent visual "
            "record, or the user asks you to save what you see."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "filename": {
                    "type": "STRING",
                    "description": "Optional filename (without extension). Defaults to a timestamp-based name.",
                },
            },
            "required": [],
        },
    ),
    types.FunctionDeclaration(
        name="open_url",
        description=(
            "Open a URL in the user's default web browser. "
            "Use to navigate to websites, documentation, or web apps."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "url": {
                    "type": "STRING",
                    "description": "The full URL to open (e.g. 'https://google.com')",
                },
            },
            "required": ["url"],
        },
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool Executor
# ─────────────────────────────────────────────────────────────────────────────


def execute_tool(name: str, args: dict) -> dict:
    """
    Route a tool call to the correct local OS function.

    Returns a dict with at minimum {"status": "success"|"error", ...}.
    All exceptions are caught so a bad tool call never crashes the session.
    """
    log.info("⚡ Executing tool: %s(%s)", name, json.dumps(args, default=str))
    omni_state.add_monologue(f"Tool call: {name}({json.dumps(args, default=str)[:80]})")

    # ── Autonomy gate — if < 80%, return confirmation-required ────────
    if omni_state.autonomy_level < 80:
        short = json.dumps(args, default=str)[:100]
        msg = (f"[LOW AUTONOMY] I want to execute {name}({short}). "
               f"Please confirm verbally.")
        log.info("🔒 %s", msg)
        omni_state.add_monologue(f"Autonomy gate: awaiting confirmation for {name}")
        result = {"status": "confirmation_required", "message": msg}
        log_action(name, args, result)
        return result

    try:
        # ── Mouse Click ──────────────────────────────────────────────────
        if name == "mouse_click":
            x = int(args["x"])
            y = int(args["y"])
            button = str(args.get("button", "left"))
            clicks = int(args.get("clicks", 1))
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            result = {
                "status": "success",
                "message": f"Clicked {button} button at ({x}, {y}) x{clicks}",
            }

        # ── Mouse Move ───────────────────────────────────────────────────
        elif name == "mouse_move":
            x = int(args["x"])
            y = int(args["y"])
            duration = float(args.get("duration", 0.3))
            pyautogui.moveTo(x=x, y=y, duration=duration)
            result = {
                "status": "success",
                "message": f"Moved mouse to ({x}, {y})",
            }

        # ── Mouse Scroll (Phase 3) ──────────────────────────────────────
        elif name == "mouse_scroll":
            scroll_clicks = int(args["clicks"])
            x = args.get("x")
            y = args.get("y")
            if x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y))
            pyautogui.scroll(scroll_clicks)
            direction = "UP" if scroll_clicks > 0 else "DOWN"
            result = {
                "status": "success",
                "message": f"Scrolled {direction} by {abs(scroll_clicks)} clicks",
            }

        # ── Mouse Drag (Phase 3) ────────────────────────────────────────
        elif name == "mouse_drag":
            sx, sy = int(args["start_x"]), int(args["start_y"])
            ex, ey = int(args["end_x"]), int(args["end_y"])
            duration = float(args.get("duration", 0.5))
            pyautogui.moveTo(sx, sy)
            pyautogui.mouseDown()
            pyautogui.moveTo(ex, ey, duration=duration)
            pyautogui.mouseUp()
            result = {
                "status": "success",
                "message": f"Dragged from ({sx},{sy}) to ({ex},{ey})",
            }

        # ── Keyboard Type ────────────────────────────────────────────────
        elif name == "keyboard_type":
            text = str(args["text"])
            interval = float(args.get("interval", 0.02))
            pyautogui.write(text, interval=interval)
            result = {
                "status": "success",
                "message": f"Typed {len(text)} characters",
            }

        # ── Keyboard Hotkey ──────────────────────────────────────────────
        elif name == "keyboard_hotkey":
            keys_str = str(args["keys"])
            keys = [k.strip() for k in keys_str.split(",")]
            pyautogui.hotkey(*keys)
            result = {
                "status": "success",
                "message": f"Pressed hotkey: {'+'.join(keys)}",
            }

        # ── Shell Command ────────────────────────────────────────────────
        elif name == "run_shell_command":
            command = str(args["command"])
            timeout = int(args.get("timeout", 30))

            # Safety check
            if SAFE_MODE:
                cmd_lower = command.lower()
                for pattern in BLOCKED_PATTERNS:
                    if pattern in cmd_lower:
                        msg = (
                            f"BLOCKED by SAFE_MODE: command contains "
                            f"'{pattern.strip()}'. Destructive operations "
                            f"are not allowed."
                        )
                        log.warning("🛡️  %s", msg)
                        result = {"status": "blocked", "message": msg}
                        stats.last_tool_action = f"🛡️ BLOCKED: {command[:60]}"
                        stats.tool_errors += 1
                        log_action(name, args, result)
                        return result

            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.path.expanduser("~"),
            )

            stdout = (proc.stdout or "")[:MAX_SHELL_OUTPUT]
            stderr = (proc.stderr or "")[:MAX_SHELL_OUTPUT]

            result = {
                "status": "success",
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }

        # ── Take Screenshot (Phase 3) ────────────────────────────────────
        elif name == "take_screenshot":
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            fname = args.get("filename") or datetime.now().strftime("screenshot_%Y%m%d_%H%M%S")
            filepath = SCREENSHOT_DIR / f"{fname}.png"

            future = SCREEN_EXECUTOR.submit(take_screenshot_sync, str(filepath))
            future.result()

            result = {
                "status": "success",
                "message": f"Screenshot saved to {filepath}",
                "path": str(filepath),
            }

        # ── Open URL (Phase 3) ───────────────────────────────────────────
        elif name == "open_url":
            url = str(args["url"])
            webbrowser.open(url)
            result = {
                "status": "success",
                "message": f"Opened {url} in default browser",
            }

        # ── Unknown Tool ─────────────────────────────────────────────────
        else:
            result = {
                "status": "error",
                "message": f"Unknown tool: {name}",
            }

        # ── Update stats & audit log ─────────────────────────────────────
        stats.tool_calls_executed += 1
        short_args = json.dumps(args, default=str)
        if len(short_args) > 60:
            short_args = short_args[:57] + "..."
        stats.last_tool_action = f"{name}({short_args}) → {result.get('status', '?')}"
        log_action(name, args, result)
        return result

    except pyautogui.FailSafeException:
        msg = (
            "pyautogui FAILSAFE triggered — mouse was moved to a screen "
            "corner. Action aborted for safety."
        )
        log.warning("🛡️  %s", msg)
        stats.tool_errors += 1
        result = {"status": "error", "message": msg}
        log_action(name, args, result)
        return result

    except subprocess.TimeoutExpired:
        result = {
            "status": "error",
            "message": f"Command timed out after {args.get('timeout', 30)}s",
        }
        stats.tool_errors += 1
        log_action(name, args, result)
        return result

    except Exception as exc:
        log.error("Tool execution error: %s", traceback.format_exc())
        stats.tool_errors += 1
        result = {
            "status": "error",
            "message": f"{type(exc).__name__}: {exc}",
        }
        log_action(name, args, result)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Async Media Tasks
# ─────────────────────────────────────────────────────────────────────────────


async def send_audio(
    session,
    mic_stream: pyaudio.Stream,
    shutdown: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Continuously read mic PCM chunks (in a thread-executor to avoid blocking
    the event loop) and stream them to the Gemini session.
    """
    log.info("Audio sender started")
    try:
        while not shutdown.is_set():
            # Kill switch — skip capture when engaged
            if omni_state.kill_switch:
                await asyncio.sleep(0.2)
                continue

            # PyAudio's read() is blocking — run in executor
            data: bytes = await loop.run_in_executor(
                None,
                partial(mic_stream.read, MIC_CHUNK, exception_on_overflow=False),
            )
            if not data:
                continue

            await session.send_realtime_input(
                audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
            )
            stats.audio_chunks_sent += 1
    except asyncio.CancelledError:
        log.info("Audio sender cancelled")
    except Exception:
        log.error("Audio sender error:\n%s", traceback.format_exc())
    finally:
        log.info("Audio sender stopped")


async def send_screen(
    session,
    shutdown: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Capture the screen at SCREEN_FPS and send JPEG frames to Gemini.
    mss is not thread-safe, so we create a fresh context inside a dedicated
    executor call each time we grab.
    """
    log.info("Screen sender started — %d FPS", SCREEN_FPS)
    interval = 1.0 / SCREEN_FPS

    try:
        while not shutdown.is_set():
            # Kill switch — skip capture when engaged
            if omni_state.kill_switch:
                await asyncio.sleep(0.5)
                continue

            # Grab + compress in executor to keep event loop free
            jpeg_bytes: bytes = await loop.run_in_executor(
                SCREEN_EXECUTOR, capture_screen_jpeg,
            )

            await session.send_realtime_input(
                video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
            )
            stats.screen_frames_sent += 1
            omni_state.add_monologue(
                f"Screen frame #{stats.screen_frames_sent} sent ({len(jpeg_bytes)} bytes)")
            log.debug("Screen frame sent — %d bytes", len(jpeg_bytes))

            # Throttle to target FPS (use live SCREEN_FPS value)
            await asyncio.sleep(1.0 / SCREEN_FPS)
    except asyncio.CancelledError:
        log.info("Screen sender cancelled")
    except Exception:
        log.error("Screen sender error:\n%s", traceback.format_exc())
    finally:
        log.info("Screen sender stopped")


async def play_audio(
    speaker_stream: pyaudio.Stream,
    audio_out_queue: asyncio.Queue,
    shutdown: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Read audio chunks from the queue and play them."""
    log.info("Audio player started")
    try:
        while not shutdown.is_set():
            try:
                data = await asyncio.wait_for(audio_out_queue.get(), timeout=0.1)
                await loop.run_in_executor(None, speaker_stream.write, data)
                audio_out_queue.task_done()
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        log.info("Audio player cancelled")
    except Exception:
        log.error("Audio player error:\n%s", traceback.format_exc())
    finally:
        log.info("Audio player stopped")


async def receive_responses(
    session,
    audio_out_queue: asyncio.Queue,
    shutdown: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Listen for server responses. Play back audio chunks through the speaker
    stream, dispatch tool calls to execute_tool, and log text.
    """
    log.info("Receiver started — awaiting Gemini responses")
    try:
        while not shutdown.is_set():
            try:
                turn = session.receive()
                async for response in turn:
                    # --- Server content (audio / text) ---
                    server_content = getattr(response, "server_content", None)
                    if server_content and server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                # Queue audio chunks to play asynchronously
                                await audio_out_queue.put(part.inline_data.data)
                            if part.text:
                                log.info("Gemini (text): %s", part.text)
                                stats.last_ai_speech = part.text
                                omni_state.add_monologue(f"AI: {part.text[:120]}")

                    # --- Turn complete signal ---
                    if server_content and server_content.turn_complete:
                        log.debug("Turn complete")

                    # --- Tool calls (OS Execution) ---
                    tool_call = getattr(response, "tool_call", None)
                    if tool_call:
                        function_responses = []
                        for fc in tool_call.function_calls:
                            result = await loop.run_in_executor(
                                None, execute_tool, fc.name, dict(fc.args),
                            )
                            log.info(
                                "Tool result [%s]: %s",
                                fc.name,
                                json.dumps(result, default=str)[:200],
                            )
                            function_responses.append(
                                types.FunctionResponse(
                                    name=fc.name,
                                    id=fc.id,
                                    response=result,
                                )
                            )
                        await session.send_tool_response(
                            function_responses=function_responses
                        )

            except StopAsyncIteration:
                break
    except asyncio.CancelledError:
        log.info("Receiver cancelled")
    except Exception:
        log.error("Receiver error:\n%s", traceback.format_exc())
    finally:
        log.info("Receiver stopped")


async def run_web_server(app, port, shutdown):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', port)
    await site.start()
    url = f"http://localhost:{port}"
    log.info("Web Dashboard available at %s", url)
    
    try:
        # Open in default browser automatically
        webbrowser.open(url)
    except Exception:
        pass

    try:
        await shutdown.wait()
    finally:
        await runner.cleanup()
        log.info("Web Dashboard stopped")


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    """Bootstrap all subsystems, connect to Gemini, and run the media loop."""

    # ── Validate API key ─────────────────────────────────────────────────
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY is not set. Please set it via Web Dashboard configuration.")

    # ── Initialise Gemini client ─────────────────────────────────────────
    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options={"api_version": "v1alpha"}
    )
    log.info("Gemini client initialised (model: %s, API: v1alpha)", GEMINI_MODEL)

    # ── Session configuration ────────────────────────────────────────────
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        ),
        system_instruction=types.Content(
            parts=[types.Part(text=get_active_system_prompt())]
        ),
        tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
    )

    # ── Initialise PyAudio ───────────────────────────────────────────────
    pa_input = pyaudio.PyAudio()
    pa_output = pyaudio.PyAudio()

    mic_stream: pyaudio.Stream | None = None
    speaker_stream: pyaudio.Stream | None = None

    # ── Shutdown plumbing ────────────────────────────────────────────────
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        log.info("Interrupt received — initiating shutdown…")
        shutdown.set()

    # Register signal handlers (works on Unix; on Windows SIGINT only)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for all signals;
            # fall back to signal.signal for SIGINT
            signal.signal(sig, lambda *_: _signal_handler())

    try:
        # ── Open audio streams ───────────────────────────────────────────
        mic_stream = open_mic_stream(pa_input)
        speaker_stream = open_speaker_stream(pa_output)

        # ── Connect to Gemini Live API ───────────────────────────────────
        log.info("Connecting to Gemini Live API…")
        
        audio_out_queue = asyncio.Queue()
        
        async with client.aio.live.connect(
            model=GEMINI_MODEL, config=config
        ) as session:
            stats.connected = True
            log.info("Connected to Gemini Live API ✓")

            # ── Launch Web server + concurrent media tasks ───────────────
            app = init_web_app()
            
            await asyncio.gather(
                run_web_server(app, 8000, shutdown),
                stats_broadcaster(shutdown),
                fatigue_monitor(shutdown),
                send_audio(session, mic_stream, shutdown, loop),
                send_screen(session, shutdown, loop),
                receive_responses(session, audio_out_queue, shutdown, loop),
                play_audio(speaker_stream, audio_out_queue, shutdown, loop),
            )

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down…")
        shutdown.set()
    except Exception:
        log.critical("Fatal error:\n%s", traceback.format_exc())
        shutdown.set()
    finally:
        stats.connected = False

        # ── Cleanup ──────────────────────────────────────────────────────
        log.info("Cleaning up resources…")
        
        try:
            SCREEN_EXECUTOR.shutdown(wait=True)
        except Exception as e:
            log.debug("SCREEN_EXECUTOR cleanup error: %s", e)

        if mic_stream and mic_stream.is_active():
            mic_stream.stop_stream()
            mic_stream.close()
            log.info("Mic stream closed")

        if speaker_stream and speaker_stream.is_active():
            speaker_stream.stop_stream()
            speaker_stream.close()
            log.info("Speaker stream closed")

        pa_input.terminate()
        pa_output.terminate()
        log.info("PyAudio terminated")

        log.info("Omni-OS shut down cleanly. Goodbye.")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
