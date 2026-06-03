#!/usr/bin/env python3
"""
Build repo-root .cursor/mcp.json from earningsedge/.env.

Run from anywhere:
  python earningsedge/scripts/sync_cursor_mcp.py

Requires in .env (see .env.example):
  FRED_API_KEY, FMP_API_KEY (or FMP_ACCESS_TOKEN)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    earningsedge = script_dir.parent
    repo_root = earningsedge.parent
    env_path = earningsedge / ".env"
    out_path = repo_root / ".cursor" / "mcp.json"

    env = load_dotenv(env_path)
    # Merge process env so CI or exports can override
    for k in ("FRED_API_KEY", "FMP_API_KEY", "FMP_ACCESS_TOKEN"):
        if os.environ.get(k):
            env[k] = os.environ[k]

    fred = env.get("FRED_API_KEY", "").strip()
    fmp = (env.get("FMP_ACCESS_TOKEN") or env.get("FMP_API_KEY", "")).strip()

    missing = [n for n, v in [("FRED_API_KEY", fred), ("FMP_API_KEY or FMP_ACCESS_TOKEN", fmp)] if not v]
    if missing:
        print("sync_cursor_mcp: missing in earningsedge/.env:", ", ".join(missing), file=sys.stderr)
        return 1

    doc = {
        "mcpServers": {
            "sec-edgar": {"command": "uvx", "args": ["sec-edgar-mcp"]},
            "finance-tools": {
                "command": "uvx",
                "args": ["finance-tools-mcp"],
                "env": {"FRED_API_KEY": fred},
            },
            "fmp-mcp": {
                "command": "npx",
                "args": ["-y", "fmp-mcp"],
                "env": {"FMP_ACCESS_TOKEN": fmp},
            },
        }
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
