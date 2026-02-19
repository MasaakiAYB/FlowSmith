#!/usr/bin/env python3
"""Thin entrypoint for the autonomous issue-to-PR pipeline."""

from __future__ import annotations

import sys

try:
    from agent_pipeline_impl import main
except ModuleNotFoundError:
    from scripts.agent_pipeline_impl import main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as err:
        print(f"[agent-pipeline] ERROR: {err}", file=sys.stderr)
        raise SystemExit(1)
