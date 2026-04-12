"""config_loader.py — CLI argument parsing and config assembly for Queue Farmer v2."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


@dataclass
class FarmerConfig:
    """Validated, fully-resolved runtime configuration."""
    total_instances: int
    account_start:   int
    server_port:     int
    anthropic_api_key: str


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Queue Farmer v2 — pre-stage FIFA queue sessions",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--instances", type=int, default=25,
        metavar="N",
        help="Total browser instances to spawn (default: 25)",
    )
    p.add_argument(
        "--account-start", type=int, default=50,
        metavar="N",
        help="Starting account index into FIFA_ACCOUNTS (default: 50)",
    )
    p.add_argument(
        "--server-port", type=int, default=9099,
        metavar="N",
        help="Local CAPTCHA solve server port (default: 9099)",
    )
    return p


def load_config(args: argparse.Namespace) -> FarmerConfig:
    """Resolve config from parsed args + environment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        # Try loading from a .env file in the project root
        _try_load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        print(
            "[CONFIG] WARNING: ANTHROPIC_API_KEY not set. "
            "Create a .env file with ANTHROPIC_API_KEY=sk-ant-... "
            "or set the environment variable before running.",
            flush=True,
        )

    return FarmerConfig(
        total_instances=args.instances,
        account_start=args.account_start,
        server_port=args.server_port,
        anthropic_api_key=api_key,
    )


def _try_load_dotenv() -> None:
    """Attempt to load .env from the script directory. Silently skipped if unavailable."""
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except ImportError:
        pass
