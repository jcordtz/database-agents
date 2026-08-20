"""Command line front-end for :mod:`db_agents.setup_builder`.

Invoked by ``scripts/create-agent-setup.sh`` (but usable standalone) to turn a
tables CSV plus per-host properties files into ``config.yaml`` and a ``.env``
skeleton in a target directory::

    python -m db_agents.setup_cli \
        --tables-csv tables.csv \
        --connections-dir connections/ \
        --target-dir /path/to/setup \
        --llm-deployment gpt-4o

It also supports ``--print-dialects`` so the shell script can work out which
optional database driver extras it needs to install.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from db_agents.config import PurviewConfig
from db_agents.setup_builder import (
    SetupBuilderError,
    build_app_config,
    config_to_yaml,
    env_skeleton,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="db-agents-setup",
        description="Generate a db-agents config.yaml and .env from a tables CSV and connection properties files.",
    )
    parser.add_argument("--tables-csv", required=True, help="CSV listing db_type,host,schema,table")
    parser.add_argument("--connections-dir", required=True, help="Directory holding <host>.properties files")
    parser.add_argument("--target-dir", help="Where to write config.yaml and .env (omit with --print-dialects)")
    parser.add_argument("--llm-deployment", help="Azure OpenAI deployment name used for descriptions and Q&A")
    parser.add_argument("--purview-endpoint", help="Purview account endpoint; enables Purview enrichment")
    parser.add_argument("--cache-path", default=".db_agents_cache.sqlite3", help="SQLite metadata cache path")
    parser.add_argument("--force", action="store_true", help="Overwrite existing config.yaml/.env")
    parser.add_argument(
        "--print-dialects",
        action="store_true",
        help="Print the distinct dialects used by the CSV (one per line) and exit without writing files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    purview = None
    if args.purview_endpoint:
        purview = PurviewConfig(enabled=True, account_endpoint=args.purview_endpoint)

    try:
        result = build_app_config(
            tables_csv=args.tables_csv,
            connections_dir=args.connections_dir,
            llm_deployment=args.llm_deployment,
            purview=purview,
        )
    except SetupBuilderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.print_dialects:
        for dialect in sorted(result.dialects):
            print(dialect)
        return 0

    if not args.target_dir:
        print("error: --target-dir is required unless --print-dialects is given", file=sys.stderr)
        return 2

    result.config.cache.path = args.cache_path

    target_dir = Path(args.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    config_path = target_dir / "config.yaml"
    env_path = target_dir / ".env"

    if config_path.exists() and not args.force:
        print(f"error: {config_path} already exists (use --force to overwrite)", file=sys.stderr)
        return 3

    config_path.write_text(config_to_yaml(result.config), encoding="utf-8")

    # Never clobber a .env that may already hold real secrets.
    if env_path.exists() and not args.force:
        print(f"note: {env_path} already exists; leaving it untouched", file=sys.stderr)
    else:
        env_path.write_text(
            env_skeleton(result, purview_enabled=purview is not None, llm_configured=bool(args.llm_deployment)),
            encoding="utf-8",
        )
        env_path.chmod(0o600)

    print(
        f"Wrote {config_path} with {len(result.config.databases)} connection(s) "
        f"covering {result.table_count} table(s)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
