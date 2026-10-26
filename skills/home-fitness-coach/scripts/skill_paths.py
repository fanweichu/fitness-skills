"""Locate the optional video skill without embedding a machine-specific path."""
import os
from pathlib import Path


def video_skill():
    override = os.environ.get('WORKOUT_SKILL_DIR')
    candidates = [Path(override).expanduser()] if override else [
        Path(__file__).resolve().parents[2] / 'analyze-workout-video',
        Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills/analyze-workout-video',
        Path.home() / '.agents/skills/analyze-workout-video',
    ]
    for candidate in candidates:
        if (candidate / 'scripts/count_evidence.py').is_file():
            return candidate.resolve()
    raise ValueError('Install analyze-workout-video or set WORKOUT_SKILL_DIR to its directory')
