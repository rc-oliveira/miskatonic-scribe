"""Standalone script to create or force-update cards.json from the ArkhamDB public API."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from miskatonic_scribe.config import AppConfig
from miskatonic_scribe.infrastructure.arkhamdb_repository import ArkhamDBCardRepository
from miskatonic_scribe.logging_config import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Baixa ou atualiza a base local cards.json a partir da API pública do ArkhamDB."
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Caminho de saída do arquivo cards.json (padrão: definido em ARKHAMDB_CARDS_PATH ou cards.json).",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        default=True,
        help="Força o download mesmo que o arquivo já exista localmente (padrão: True).",
    )
    args = parser.parse_args()

    cfg = AppConfig.from_env()
    configure_logging(cfg.log_level)

    target_path = args.output or cfg.arkhamdb_cards_path
    repo = ArkhamDBCardRepository(
        cards_path=target_path,
        api_url=cfg.arkhamdb_api_url,
        refresh_days=cfg.arkhamdb_refresh_days,
    )
    records = repo.load_cards(force_reload=args.force)
    print(f"cards.json atualizado com sucesso em '{target_path}' ({len(records)} cartas válidas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
