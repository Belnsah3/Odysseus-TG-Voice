"""Small PCM helpers: resampling and framing. PCM is always s16le mono."""
from __future__ import annotations

import numpy as np


def pcm_bytes_to_np(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.int16)


def np_to_pcm_bytes(arr: np.ndarray) -> bytes:
    return arr.astype(np.int16).tobytes()


def resample(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear resample of s16le mono PCM. Good enough for speech."""
    if src_rate == dst_rate or not data:
        return data
    x = pcm_bytes_to_np(data).astype(np.float32)
    if x.size == 0:
        return data
    n_dst = int(round(x.size * dst_rate / src_rate))
    if n_dst <= 0:
        return b""
    src_idx = np.linspace(0, x.size - 1, num=n_dst)
    y = np.interp(src_idx, np.arange(x.size), x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


def frame_iter(buffer: bytes, frame_bytes: int):
    """Yield fixed-size frames; returns the remainder as the final partial value.

    Usage:
        for frame in frame_iter(buf, fb): ...
    Remainder handling is done by the caller keeping leftover bytes.
    """
    n = len(buffer) // frame_bytes
    for i in range(n):
        yield buffer[i * frame_bytes:(i + 1) * frame_bytes]


def frame_size_bytes(sample_rate: int, frame_ms: int, channels: int = 1) -> int:
    return int(sample_rate * frame_ms / 1000) * 2 * channels


def mono_to_stereo(data: bytes) -> bytes:
    """Convert s16le mono PCM to s16le stereo by duplicating the channel."""
    if not data:
        return data
    arr = np.frombuffer(data, dtype=np.int16)
    stereo = np.repeat(arr.reshape(-1, 1), 2, axis=1)
    return stereo.tobytes()


def stereo_to_mono(data: bytes) -> bytes:
    """Downmix s16le stereo PCM to mono by averaging L and R."""
    if not data:
        return data
    arr = np.frombuffer(data, dtype=np.int16).astype(np.int32)
    if arr.size % 2 != 0:
        arr = arr[:-1]
    mono = (arr[0::2] + arr[1::2]) // 2
    return mono.astype(np.int16).tobytes()
