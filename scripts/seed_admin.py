#!/usr/bin/env python3
"""Bootstrap CLI script to seed an initial administrator in users.json."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, Sequence

# Ensure 'src' is importable when running `python scripts/seed_admin.py` directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from miskatonic_scribe.infrastructure.iam_managers import UserManager


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera ou atualiza o arquivo users.json inserindo um administrador aprovado."
    )
    parser.add_argument(
        "--id",
        required=True,
        dest="telegram_id",
        help="Telegram ID numérico do administrador (ex: 1725371455)",
    )
    parser.add_argument(
        "--name",
        required=True,
        dest="first_name",
        help="Nome do administrador (ex: Rodrigo)",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Username opcional do Telegram (sem @)",
    )
    parser.add_argument(
        "--file",
        default=None,
        dest="file_path",
        help="Caminho para o arquivo users.json (padrão: USERS_FILE_PATH ou users.json)",
    )
    return parser


async def seed_admin_user(
    telegram_id: str,
    first_name: str,
    username: Optional[str] = None,
    file_path: Optional[Path | str] = None,
) -> dict:
    """Create or update the admin user in users.json using UserManager and asyncio.Lock()."""
    explicit_file = file_path is not None or bool(os.getenv("USERS_FILE_PATH"))
    target_path = Path(file_path or os.getenv("USERS_FILE_PATH", "users.json"))

    manager = UserManager(file_path=target_path)
    record = await manager.seed_admin(
        telegram_id=telegram_id,
        first_name=first_name,
        username=username,
    )

    # Also mirror to ./data/users.json if running on host with a ./data Docker volume directory
    data_dir = Path("data")
    if not explicit_file and data_dir.is_dir() and target_path.resolve() != (data_dir / "users.json").resolve():
        docker_manager = UserManager(file_path=data_dir / "users.json")
        await docker_manager.seed_admin(
            telegram_id=telegram_id,
            first_name=first_name,
            username=username,
        )

    return record


def main(
    argv: Optional[Sequence[str]] = None,
    runner: Optional[Callable[[Coroutine[Any, Any, Any]], Any]] = None,
) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    coro = seed_admin_user(
        telegram_id=str(args.telegram_id).strip(),
        first_name=str(args.first_name).strip(),
        username=args.username.strip().lstrip("@") if args.username else None,
        file_path=args.file_path,
    )
    if runner is not None:
        record = runner(coro)
    else:
        record = asyncio.run(coro)
    target = args.file_path or os.getenv("USERS_FILE_PATH", "users.json")
    print(
        f"✅ Admin '{record['first_name']}' (ID: {args.telegram_id}, status={record['status']}, "
        f"role={record['role']}) gravado com sucesso em '{target}'."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
