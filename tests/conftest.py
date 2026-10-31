"""Shared pytest fixtures simulating ArkhamDB data and test configurations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import pytest


@pytest.fixture
def sample_arkhamdb_payload() -> List[dict[str, Any]]:
    """Representative ArkhamDB cards payload including investigators, deck_options, XP levels, reprints, and subtitles."""
    return [
        {
            "code": "05004",
            "name": "Diana Stanley",
            "real_name": "Diana Stanley",
            "subname": "The Redeemed Cultist",
            "xp": 0,
            "pack_code": "tcu",
            "faction_code": "mystic",
            "type_code": "investigator",
            "octgn_id": "diana-guid-05004",
            "deck_requirements": {
                "size": 35,
                "card": {
                    "05013": {"05013": "05013"},
                    "05014": {"05014": "05014"},
                    "05015": {"05015": "05015"},
                },
                "random": [{"target": "subtype", "value": "basicweakness"}],
            },
            "deck_options": [
                {"faction": ["mystic"], "level": {"min": 0, "max": 5}},
                {"faction": ["guardian"], "level": {"min": 0, "max": 2}},
                {"faction": ["neutral"], "level": {"min": 0, "max": 5}},
            ],
        },
        {
            "code": "01020",
            "name": "Facão",
            "real_name": "Machete",
            "xp": 0,
            "pack_code": "core",
            "faction_code": "guardian",
            "type_code": "asset",
            "octgn_id": "machete-guid-01020",
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
            "octgn_id": "shrivelling-guid-01060",
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
            "octgn_id": "shrivelling-guid-02066",
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
            "code": "03034",
            "name": "Astral Travel",
            "real_name": "Astral Travel",
            "xp": 0,
            "pack_code": "ptc",
            "faction_code": "mystic",
            "type_code": "event",
        },
        {
            "code": "02107",
            "name": "Foi só um arranhão...",
            "real_name": "I've had worse…",
            "xp": 4,
            "pack_code": "dwl",
            "faction_code": "guardian",
            "type_code": "event",
        },
        {
            "code": "02261",
            "name": "Foi só um arranhão...",
            "real_name": "I've had worse…",
            "xp": 2,
            "pack_code": "dwl",
            "faction_code": "guardian",
            "type_code": "event",
        },
        {
            "code": "11054",
            "name": '"You\'ve had worse..."',
            "real_name": '"You\'ve had worse..."',
            "xp": 0,
            "pack_code": "tdc",
            "faction_code": "seeker",
            "type_code": "event",
        },
        {
            "code": "60112",
            "name": "Counterpunch",
            "real_name": "Counterpunch",
            "xp": 0,
            "pack_code": "nat",
            "faction_code": "guardian",
            "type_code": "event",
        },
        {
            "code": "04110",
            "name": "Contramágica",
            "real_name": "Counterspell",
            "xp": 2,
            "pack_code": "tfa",
            "faction_code": "mystic",
            "type_code": "event",
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
