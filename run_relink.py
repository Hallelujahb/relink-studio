#!/usr/bin/env python3
"""Cross-platform entrypoint for Relink Studio's backend. Same job as
run_relink.sh, but works on native Windows too (no WSL needed) since
it's plain Python instead of bash.

Usage:
    python run_relink.py --local
    python run_relink.py --lan
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--local", "--lan"):
        print("Usage: python run_relink.py --local | --lan", file=sys.stderr)
        sys.exit(1)

    os.environ["RELINK_MODE"] = "local" if sys.argv[1] == "--local" else "lan"

    script_dir = Path(__file__).resolve().parent
    api_dir = script_dir / "relink-api"
    if not api_dir.is_dir():
        print(f"Could not find relink-api/ next to this script (looked in {api_dir}).", file=sys.stderr)
        sys.exit(1)

    venv_dir = api_dir / "venv"
    is_windows = os.name == "nt"
    bin_dir = "Scripts" if is_windows else "bin"
    python_bin = venv_dir / bin_dir / ("python.exe" if is_windows else "python")

    if not venv_dir.is_dir():
        print("Creating virtualenv (first run only)...")
        venv.EnvBuilder(with_pip=True).create(venv_dir)

    subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
        cwd=api_dir, check=True,
    )

    if os.environ["RELINK_MODE"] == "lan":
        print("Starting Relink in LAN mode -- reachable from other devices on your")
        print("private network. Do NOT forward this port through your router to")
        print("the public internet.")

    result = subprocess.run([str(python_bin), "run.py"], cwd=api_dir)
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()
