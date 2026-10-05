"""PCM helpers. Vobiz streams linear16 mono: inbound payloads are BIG-endian, outbound (playAudio) must be LITTLE-endian."""
import sys
from array import array
from typing import Iterator


def swap_endian16(data: bytes) -> bytes:
    """Swap byte order of 16-bit samples (Vobiz inbound big-endian <-> the little-endian Deepgram expects)."""
    data = data[: len(data) // 2 * 2]
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder == "little":
        samples.byteswap()
    return samples.tobytes()


def frame_bytes(sample_rate: int, ms: int = 20) -> int:
    return sample_rate * 2 * ms // 1000


def iter_frames(pcm: bytes, size: int) -> Iterator[bytes]:
    for i in range(0, len(pcm), size):
        yield pcm[i : i + size]


def pcm_seconds(num_bytes: int, sample_rate: int) -> float:
    return num_bytes / (sample_rate * 2)
