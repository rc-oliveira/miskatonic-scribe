"""Local audio file storage and automated maintenance routine."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Callable, List

from miskatonic_scribe.config import AudioMaintenanceConfig
from miskatonic_scribe.domain.exceptions import AudioStorageError
from miskatonic_scribe.domain.interfaces import IAudioStorageManager

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_EXTENSIONS = {".ogg", ".oga", ".mp3", ".wav", ".m4a"}


class LocalAudioStorageManager(IAudioStorageManager):
    """Manages local voice/audio files with count-based and age-based retention."""

    def __init__(
        self,
        config: AudioMaintenanceConfig,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._clock = clock
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        try:
            self._config.storage_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create audio storage directory %s: %s", self._config.storage_dir, exc)
            raise AudioStorageError(f"Could not create audio directory: {exc}") from exc

    def _list_audio_files(self) -> List[Path]:
        if not self._config.storage_dir.exists():
            return []
        files = [
            p
            for p in self._config.storage_dir.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        ]
        # Sort oldest first by modification time
        files.sort(key=lambda p: p.stat().st_mtime)
        return files

    def run_maintenance(self) -> List[Path]:
        """Clean up expired audio files (> X days) or when stored count reaches max_files (e.g. 5)."""
        self._ensure_directory()
        removed_files: List[Path] = []
        now = self._clock()
        max_age_seconds = self._config.max_age_days * 86400

        try:
            audio_files = self._list_audio_files()
        except OSError as exc:
            logger.warning("Could not list audio directory during maintenance: %s", exc)
            return removed_files

        # 1. Remove files older than max_age_days
        remaining_files: List[Path] = []
        for file_path in audio_files:
            try:
                file_age = now - file_path.stat().st_mtime
                if file_age >= max_age_seconds:
                    file_path.unlink(missing_ok=True)
                    removed_files.append(file_path)
                    logger.info(
                        "Audio maintenance (age policy): removed expired file '%s' (age=%.1fd)",
                        file_path.name,
                        file_age / 86400,
                    )
                else:
                    remaining_files.append(file_path)
            except OSError as exc:
                logger.warning("Failed to delete expired audio file '%s': %s", file_path, exc)

        # 2. If remaining files reach or exceed max_files (default 5), purge oldest files
        if len(remaining_files) >= self._config.max_files:
            # Purge all or oldest so we never exceed max_files
            files_to_purge = remaining_files[: len(remaining_files) - self._config.max_files + 1]
            # Wait: the prompt states "limpar a cada 5 arquivos armazenados ou X dias".
            # If we have >= max_files stored, let's clean all completed older files!
            for file_path in remaining_files:
                try:
                    file_path.unlink(missing_ok=True)
                    removed_files.append(file_path)
                    logger.info(
                        "Audio maintenance (count threshold=%d): purged stored file '%s'",
                        self._config.max_files,
                        file_path.name,
                    )
                except OSError as exc:
                    logger.warning("Failed to purge audio file '%s': %s", file_path, exc)

        return removed_files

    def prepare_audio_path(self, file_unique_id: str, extension: str = ".ogg") -> Path:
        """Run maintenance before allocating a new local .ogg path."""
        self.run_maintenance()
        ext = extension if extension.startswith(".") else f".{extension}"
        safe_id = "".join(ch for ch in file_unique_id if ch.isalnum() or ch in ("-", "_"))
        if not safe_id:
            safe_id = uuid.uuid4().hex[:12]
        filename = f"voice_{int(self._clock())}_{safe_id}{ext}"
        return self._config.storage_dir / filename
