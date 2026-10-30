"""Tests for LocalAudioStorageManager (count-based and age-based maintenance)."""

from __future__ import annotations

import os
from pathlib import Path

from miskatonic_scribe.config import AudioMaintenanceConfig
from miskatonic_scribe.infrastructure.audio_storage import LocalAudioStorageManager


def test_maintenance_purges_when_reaching_5_files(tmp_path: Path) -> None:
    """When 5 audio files are stored, prepare_audio_path must trigger cleanup of the stored batch."""
    audio_dir = tmp_path / "temp_audio"
    cfg = AudioMaintenanceConfig(storage_dir=audio_dir, max_files=5, max_age_days=7)
    current_time = 1_700_000_000.0
    manager = LocalAudioStorageManager(config=cfg, clock=lambda: current_time)

    # Create 5 .ogg files
    created = []
    for i in range(5):
        p = manager.prepare_audio_path(file_unique_id=f"id_{i}")
        p.write_bytes(b"OggS-fake-audio")
        os.utime(p, (current_time + i, current_time + i))
        created.append(p)

    assert len(list(audio_dir.glob("*.ogg"))) == 5

    # Preparing the 6th file triggers maintenance and cleans the 5 stored files
    sixth_path = manager.prepare_audio_path(file_unique_id="id_6")
    sixth_path.write_bytes(b"OggS-new")

    remaining = list(audio_dir.glob("*.ogg"))
    assert len(remaining) == 1
    assert remaining[0] == sixth_path


def test_maintenance_removes_files_older_than_x_days(tmp_path: Path) -> None:
    """Files older than max_age_days are deleted even if total count is below max_files."""
    audio_dir = tmp_path / "temp_audio"
    cfg = AudioMaintenanceConfig(storage_dir=audio_dir, max_files=5, max_age_days=3)
    now = 1_700_500_000.0
    manager = LocalAudioStorageManager(config=cfg, clock=lambda: now)

    old_file = audio_dir / "voice_old.ogg"
    old_file.write_bytes(b"old")
    four_days_ago = now - (4 * 86400)
    os.utime(old_file, (four_days_ago, four_days_ago))

    fresh_file = audio_dir / "voice_fresh.ogg"
    fresh_file.write_bytes(b"fresh")
    one_day_ago = now - (1 * 86400)
    os.utime(fresh_file, (one_day_ago, one_day_ago))

    removed = manager.run_maintenance()

    assert old_file in removed
    assert not old_file.exists()
    assert fresh_file.exists()


def test_prepare_audio_path_sanitizes_id(tmp_path: Path) -> None:
    """Unsafe characters in file_unique_id are stripped safely."""
    audio_dir = tmp_path / "temp_audio"
    cfg = AudioMaintenanceConfig(storage_dir=audio_dir, max_files=5, max_age_days=3)
    manager = LocalAudioStorageManager(config=cfg, clock=lambda: 1000.0)

    path = manager.prepare_audio_path(file_unique_id="../../../etc/passwd", extension="ogg")
    assert path.parent == audio_dir
    assert path.suffix == ".ogg"
    assert "etcpasswd" in path.name
