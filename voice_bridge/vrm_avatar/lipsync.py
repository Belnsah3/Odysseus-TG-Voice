"""Lip sync module - converts audio/text to viseme sequences.

Uses simple heuristics for Russian phonemes.
For production, integrate with Rhubarb Lip Sync or similar.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Viseme mapping for Russian phonemes
_RU_VISEME_MAP = {
    "a": "aa", "e": "E", "i": "ih", "o": "oh", "u": "ou",
    "y": "ih", "э": "E", "ю": "ou", "я": "aa", "ё": "oh",
    "п": "PP", "б": "PP", "м": "PP",
    "ф": "FF", "в": "FF",
    "т": "TH", "д": "TH", "н": "nn", "л": "nn",
    "с": "SS", "з": "SS", "ц": "SS",
    "ш": "CH", "щ": "CH", "ж": "CH", "ч": "CH",
    "р": "RR", "к": "kk", "г": "kk", "х": "kk",
}

# Punctuation to viseme mapping
_PUNCT_PAUSE = {".": "sil", "!": "sil", "?": "sil", ",": "sil", " ": "sil"}


def text_to_visemes(text: str) -> List[Tuple[str, float]]:
    """Convert text to a sequence of (viseme, duration_ms) tuples.

    Simple heuristic: each character maps to ~60ms of speech.
    For production use, integrate with a proper phoneme aligner.
    """
    visemes = []
    text = text.lower().strip()

    for char in text:
        if char in _PUNCT_PAUSE:
            visemes.append(("sil", 80))
        elif char in _RU_VISEME_MAP:
            visemes.append((_RU_VISEME_MAP[char], 60))
        elif char.isalpha():
            visemes.append(("aa", 60))
        # skip non-alpha

    return visemes


def audio_to_visemes_stub(pcm: bytes, sample_rate: int = 48000) -> List[str]:
    """Stub: analyze audio amplitude to approximate visemes.

    In production, use a proper VAD/phoneme detector.
    """
    import numpy as np

    if len(pcm) < 2:
        return ["sil"]

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples ** 2)))

    if rms < 300:
        return ["sil"]
    elif rms < 1000:
        return ["PP"]
    elif rms < 3000:
        return ["E"]
    else:
        return ["aa"]
