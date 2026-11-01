#!/usr/bin/env python3
"""Bootstrap CLI script to seed an initial administrator in users.json.

This script is strictly self-contained using only the Python standard library
so it can be run on any host or container without requiring external dependencies (e.g. pydantic).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional, Sequence


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
        help="Username opcional do Telegram (com ou sem @)",
    )
    parser.add_argument(
        "--file",
        default=None,
        dest="file_path",
        help="Caminho para o arquivo users.json (padrão: USERS_FILE_PATH ou users.json)",
    )
    return parser


def seed_admin_record(
    target_path: Path,
    telegram_id: str | int,
    first_name: str,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically insert or update an admin user in target_path using only standard library."""
    tid = str(telegram_id).strip()
    if not tid:
        raise ValueError("telegram_id must be non-empty.")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    users: Dict[str, Any] = {}
    if target_path.exists():
        try:
            raw_text = target_path.read_text(encoding="utf-8").strip()
            if raw_text:
                data = json.loads(raw_text)
                if isinstance(data, dict):
                    users = {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            users = {}

    existing = users.get(tid, {})
    clean_username = username.strip().lstrip("@") if username else existing.get("username")
    now_iso = datetime.now(timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "first_name": (first_name or "Admin").strip(),
        "username": clean_username,
        "status": "approved",
        "role": "admin",
        "created_at": existing.get("created_at") or now_iso,
    }
    users[tid] = record

    tmp_path = target_path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    tmp_path.replace(target_path)
    return record


async def seed_admin_user(
    telegram_id: str | int,
    first_name: str,
    username: Optional[str] = None,
    file_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Async wrapper for compatibility with async pipelines and tests."""
    return seed_admin_sync(
        telegram_id=telegram_id,
        first_name=first_name,
        username=username,
        file_path=file_path,
    )


def seed_admin_sync(
    telegram_id: str | int,
    first_name: str,
    username: Optional[str] = None,
    file_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Create or update admin record in target users.json and mirror to data/users.json if present."""
    explicit_file = file_path is not None or bool(os.getenv("USERS_FILE_PATH"))
    target_path = Path(file_path or os.getenv("USERS_FILE_PATH", "users.json"))

    record = seed_admin_record(
        target_path=target_path,
        telegram_id=telegram_id,
        first_name=first_name,
        username=username,
    )

    # Mirror to ./data/users.json if running on host with a ./data Docker volume directory
    data_dir = Path("data")
    if not explicit_file and data_dir.is_dir():
        docker_path = (data_dir / "users.json").resolve()
        if target_path.resolve() != docker_path:
            seed_admin_record(
                target_path=data_dir / "users.json",
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

    clean_user = args.username.strip().lstrip("@") if args.username else None
    if runner is not None:
        record = runner(
            seed_admin_user(
                telegram_id=str(args.telegram_id).strip(),
                first_name=str(args.first_name).strip(),
                username=clean_user,
                file_path=args.file_path,
            )
        )
    else:
        record = seed_admin_sync(
            telegram_id=str(args.telegram_id).strip(),
            first_name=str(args.first_name).strip(),
            username=clean_user,
            file_path=args.file_path,
        )

    target = args.file_path or os.getenv("USERS_FILE_PATH", "users.json")
    msg = (
        f"[OK] Admin '{record['first_name']}' (ID: {args.telegram_id}, username={record.get('username')}, "
        f"status={record['status']}, role={record['role']}) gravado com sucesso em '{target}'."
    )
    try:
        print(f"✅ {msg.removeprefix('[OK] ')}")
    except UnicodeEncodeError:
        print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
