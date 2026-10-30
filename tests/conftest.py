"""Shared pytest fixtures simulating ArkhamDB data and test configurations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import pytest


@pytest.fixture
def sample_arkhamdb_payload() -> List[dict[str, Any]]:
    """Representative ArkhamDB cards payload including XP levels, reprints, and subtitles."""
    return [
        {
            "code": "01020",
            "name": "Facão",
            "real_name": "Machete",
            "xp": 0,
            "pack_code": "core",
            "faction_code": "guardian",
            "type_code": "asset",
        },
        {
            "code": "01520",
            "name": "Facão",
            "real_name": "Machete",
            "xp": 0,
            "pack_code": "rcore",
            "faction_code": "guardian",
            "type_code": "asset",
            "duplicate_of_code": "01020",
        },
        {
            "code": "01060",
            "name": "Definhar",
            "real_name": "Shrivelling",
            "xp": 0,
            "pack_code": "core",
            "faction_code": "mystic",
            "type_code": "asset",
        },
        {
            "code": "01560",
            "name": "Definhar",
            "real_name": "Shrivelling",
            "xp": 0,
            "pack_code": "rcore",
            "faction_code": "mystic",
            "type_code": "asset",
            "duplicate_of_code": "01060",
        },
        {
            "code": "02066",
            "name": "Definhar",
            "real_name": "Shrivelling",
            "xp": 3,
            "pack_code": "dwl",
            "faction_code": "mystic",
            "type_code": "asset",
        },
        {
            "code": "03306",
            "name": "Definhar",
            "real_name": "Shrivelling",
            "xp": 5,
            "pack_code": "ptc",
            "faction_code": "mystic",
            "type_code": "asset",
        },
        {
            "code": "01033",
            "name": "Dr. Milan Christopher",
            "real_name": "Dr. Milan Christopher",
            "subname": "Professor of Entomology",
            "xp": 0,
            "pack_code": "core",
            "faction_code": "seeker",
            "type_code": "asset",
        },
        {
            "code": "03192",
            "name": "Archaic Glyphs",
            "real_name": "Archaic Glyphs",
            "subname": "Guiding Stones",
            "xp": 3,
            "pack_code": "ptc",
            "faction_code": "seeker",
            "type_code": "asset",
        },
        {
            "code": "03193",
            "name": "Archaic Glyphs",
            "real_name": "Archaic Glyphs",
            "subname": "Prophecy Foretold",
            "xp": 3,
            "pack_code": "ptc",
            "faction_code": "seeker",
            "type_code": "asset",
        },
    ]


@pytest.fixture
def cards_json_file(tmp_path: Path, sample_arkhamdb_payload: List[dict[str, Any]]) -> Path:
    """Create a temporary cards.json file with sample ArkhamDB records."""
    path = tmp_path / "cards.json"
    path.write_text(json.dumps(sample_arkhamdb_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
