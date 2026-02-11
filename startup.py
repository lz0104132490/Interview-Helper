#!/usr/bin/env python3
"""
Launcher for the Interview Snapshot Relay project.

Replicates the PowerShell helper in Python so you can start/stop
the Go server and the Windows desktop agent with one command.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

WINDOWS = os.name == "nt"
SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_DIR = SCRIPT_DIR / "server"
AGENT_DIR = SCRIPT_DIR / "desktop-agent"
STATE_FILE = SCRIPT_DIR / ".startup-state.json"


def ensure_windows() -> None:
    if not WINDOWS:
        print("This launcher currently supports Windows only.", file=sys.stderr)
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start or stop the Go relay server and desktop agent."
    )
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Only affect the server (start/stop).",
    )
    parser.add_argument(
        "--agent-only",
        action="store_true",
        help="Only affect the desktop agent (start/stop).",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Launch new PowerShell windows minimized.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop tracked components instead of starting them.",
    )
    return parser.parse_args()


def validate_paths() -> None:
    if not SERVER_DIR.exists():
        print(f"Server folder not found at '{SERVER_DIR}'.", file=sys.stderr)
        sys.exit(1)
    if not AGENT_DIR.exists():
        print(f"Desktop agent folder not found at '{AGENT_DIR}'.", file=sys.stderr)
        sys.exit(1)


def new_state() -> Dict[str, Optional[Dict[str, Any]]]:
    return {"Server": None, "Agent": None}


def load_state() -> Dict[str, Optional[Dict[str, Any]]]:
    if not STATE_FILE.exists():
        return new_state()
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"Warning: state file '{STATE_FILE}' is invalid. Resetting tracker.")
        return new_state()

    state = new_state()
    if isinstance(payload.get("Server"), dict):
        state["Server"] = payload["Server"]
    if isinstance(payload.get("Agent"), dict):
        state["Agent"] = payload["Agent"]
    return state


def save_state(state: Dict[str, Optional[Dict[str, Any]]]) -> None:
    if state["Server"] or state["Agent"]:
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    elif STATE_FILE.exists():
        STATE_FILE.unlink()


def pwsh_literal(value: str) -> str:
    """Return the value as a PowerShell single-quoted literal."""
    sanitized = value.replace("'", "''")
    return f"'{sanitized}'"


def taskkill(pid: int) -> bool:
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    lower = (result.stderr or "").lower()
    if "not found" in lower:
        return False
    print(f"taskkill failed for PID {pid}: {result.stderr.strip()}", file=sys.stderr)
    return False


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if not WINDOWS:
        return False

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        success = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not success:
            return False
        STILL_ACTIVE = 259
        return exit_code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def stop_tracked_process(entry: Dict[str, Any], label: str) -> bool:
    if not entry:
        return False
    pid = int(entry.get("Pid") or 0)
    if pid <= 0:
        print(f"{label} PID missing from state.")
        return False
    if not is_process_alive(pid):
        print(f"{label} already stopped.")
        return False
    if taskkill(pid):
        print(f"{label} (PID {pid}) terminated.")
        return True
    print(f"Unable to terminate {label} (PID {pid}). See errors above.")
    return False


def stop_components(state: Dict[str, Optional[Dict[str, Any]]], stop_server: bool, stop_agent: bool) -> None:
    stopped_any = False
    if stop_server and state["Server"]:
        if stop_tracked_process(state["Server"], "Server"):
            state["Server"] = None
            stopped_any = True
    if stop_agent and state["Agent"]:
        if stop_tracked_process(state["Agent"], "Agent"):
            state["Agent"] = None
            stopped_any = True
    if not stopped_any:
        print("No tracked processes matched the requested targets.")
    else:
        print("Requested components terminated.")
    save_state(state)


def start_powershell_window(title: str, cwd: Path, payload: str, minimized: bool) -> subprocess.Popen:
    command_segments = [
        f"[console]::Title = {pwsh_literal(title)}",
        f"Set-Location -LiteralPath {pwsh_literal(str(cwd))}",
        payload,
    ]
    command = "; ".join(command_segments)
    args = [
        "powershell.exe",
        "-NoLogo",
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
    ]

    creationflags = subprocess.CREATE_NEW_CONSOLE
    startupinfo = None
    if minimized:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # 7 == SW_SHOWMINNOACTIVE
        startupinfo.wShowWindow = 7

    return subprocess.Popen(args, cwd=str(cwd), creationflags=creationflags, startupinfo=startupinfo)


def resolve_agent_python() -> str:
    venv_python = AGENT_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    python_cmd = shutil.which("python")
    if python_cmd:
        return python_cmd
    raise RuntimeError(
        "Python executable not found. Create '.venv' in 'desktop-agent' or install Python 3.10+ and ensure it is on PATH."
    )


def ensure_command_available(name: str, help_message: str) -> None:
    if shutil.which(name) is None:
        print(help_message, file=sys.stderr)
        sys.exit(1)


def record_process(state: Dict[str, Optional[Dict[str, Any]]], key: str, proc: subprocess.Popen, title: str) -> None:
    state[key] = {
        "Pid": proc.pid,
        "Title": title,
        "Started": datetime.now().isoformat(),
    }


def main() -> int:
    ensure_windows()
    args = parse_args()

    if args.server_only and args.agent_only:
        print("Cannot combine --server-only and --agent-only.", file=sys.stderr)
        return 1

    start_server = not args.agent_only
    start_agent = not args.server_only

    if not start_server and not start_agent:
        print("Nothing to do. Drop the conflicting switches or use defaults.")
        return 0

    validate_paths()
    state = load_state()

    if args.stop:
        stop_components(state, stop_server=start_server, stop_agent=start_agent)
        return 0

    if start_server and state["Server"] and is_process_alive(int(state["Server"].get("Pid", 0))):
        print(
            f"Server already tracked under PID {state['Server']['Pid']}. Use --stop or close it first.",
            file=sys.stderr,
        )
        start_server = False
    if start_agent and state["Agent"] and is_process_alive(int(state["Agent"].get("Pid", 0))):
        print(
            f"Agent already tracked under PID {state['Agent']['Pid']}. Use --stop or close it first.",
            file=sys.stderr,
        )
        start_agent = False

    launched = False

    if start_server:
        ensure_command_available("go", "Go SDK is required to launch the server. Install from https://go.dev/dl/ .")
        print("Starting Go relay server...")
        server_proc = start_powershell_window(
            title="Interview Relay - Server",
            cwd=SERVER_DIR,
            payload="go run .",
            minimized=args.minimized,
        )
        record_process(state, "Server", server_proc, "Interview Relay - Server")
        launched = True

    if start_agent:
        try:
            python_exe = resolve_agent_python()
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Starting desktop agent with '{python_exe}'...")
        agent_payload = f"& {pwsh_literal(python_exe)} main.py"
        agent_proc = start_powershell_window(
            title="Interview Relay - Agent",
            cwd=AGENT_DIR,
            payload=agent_payload,
            minimized=args.minimized,
        )
        record_process(state, "Agent", agent_proc, "Interview Relay - Agent")
        launched = True

    save_state(state)

    if launched:
        print("Launch requests sent. Inspect the spawned PowerShell windows for live logs.")
        return 0

    print("No new windows were started. Existing tracked processes may already be running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

