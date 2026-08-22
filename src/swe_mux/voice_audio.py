"""Joining a speech stream's segment WAVs into the single clip they always were.

Streaming synthesis exists for latency: a reply is cut into segments so the first
one can play while the rest are still being made. That is a production detail, not
a fact about the reply, and every surface downstream of it - the clip list, the
transcript's per-message play button, the byte cap, a download - wants one clip per
reply. This module is the seam where the segments become that clip again, once the
stream is complete and nothing more can be appended.

Two invariants make the join safe to run behind live playback:

- **It never writes over a source file.** The joined audio is written to a new
  path, so a browser mid-download of a segment keeps reading the bytes it started
  on (and on Windows the source cannot be replaced at all while it is open).
- **It refuses rather than guesses.** Segments joined at differing sample rates,
  channel counts, or sample widths would play as noise or as chipmunk speech, so a
  mismatch returns False and the caller keeps the segments. That is not
  hypothetical: the engine and voice are read from config per segment, so a voice
  changed mid-stream can produce exactly that.
"""

from __future__ import annotations

import logging
import wave
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

log = logging.getLogger(__name__)

# Copy granularity. Kokoro's 24 kHz mono 16-bit output is ~48 KB/s, so this is a
# second and a half of audio per read: large enough that a minute-long reply is a
# handful of reads, small enough that nothing here needs a whole clip in memory.
_FRAME_CHUNK = 32_768


def wav_profile(path: Path) -> tuple[int, int, int, str] | None:
    """The four parameters two WAVs must share to be concatenable, or None."""
    try:
        with wave.open(str(path), "rb") as source:
            return (
                source.getnchannels(),
                source.getsampwidth(),
                source.getframerate(),
                source.getcomptype(),
            )
    except (OSError, wave.Error, EOFError):
        return None


def join_wav_files(sources: Sequence[Path], destination: Path) -> bool:
    """Concatenate `sources` in order into `destination`.

    Returns True when `destination` holds every source's audio. Returns False -
    having removed any partial output - when there is nothing to join, a source is
    missing or unreadable, or the sources disagree on channels, sample width,
    sample rate, or compression. A False result is not an error to report to the
    operator: the caller keeps the per-segment clips, which play correctly in
    sequence, and the only thing lost is the collapse into one file.
    """
    if not sources:
        return False
    profiles = [wav_profile(path) for path in sources]
    if any(profile is None for profile in profiles):
        log.warning(
            "voice join refused: unreadable segment among %d sources", len(sources)
        )
        return False
    if len(set(profiles)) != 1:
        log.warning("voice join refused: segment audio profiles differ %s", profiles)
        return False
    profile = profiles[0]
    assert profile is not None  # narrowed by the None check above
    channels, width, rate, _comptype = profile
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with wave.open(str(destination), "wb") as sink:
            sink.setnchannels(channels)
            sink.setsampwidth(width)
            sink.setframerate(rate)
            for path in sources:
                with wave.open(str(path), "rb") as source:
                    # unsupervised-loop-ok: a bounded synchronous read of one file
                    # to its end, on a worker thread; it is not a daemon loop.
                    while True:
                        frames = source.readframes(_FRAME_CHUNK)
                        if not frames:
                            break
                        sink.writeframes(frames)
    except (OSError, wave.Error, EOFError):
        # A half-written join is worse than no join: it would be stored as the
        # reply's audio and truncate it silently.
        with suppress(OSError):
            destination.unlink(missing_ok=True)
        log.warning("voice join failed writing %s", destination, exc_info=True)
        return False
    return True
