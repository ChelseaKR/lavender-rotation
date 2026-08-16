"""Turn recommendations into a track list, and serialise it to portable formats.

These are the **credential-free** exports: they work for everyone, with no
connected account and no network. Each format carries the artist, its rank, and
a short honest "why" so the values-aware, sourced-not-inferred posture survives
the hand-off to whatever the user pastes the list into.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence

from pipeline.models import Recommendation
from recommender.why import why_this_artist

from export.models import ExportFormat, PlaylistTrack


def recommendations_to_tracks(recs: Sequence[Recommendation]) -> list[PlaylistTrack]:
    """Map each recommendation to a :class:`PlaylistTrack`, preserving order.

    The ``query`` is what a streaming provider would search; ``why`` is the
    recommendation's headline reason plus its rank-shift statement, kept for
    transparency in every export.
    """
    tracks: list[PlaylistTrack] = []
    for rec in recs:
        why = why_this_artist(rec)
        tracks.append(
            PlaylistTrack(
                artist_name=rec.artist.name,
                artist_id=rec.artist.artist_id,
                rank=rec.rank,
                query=f'artist:"{rec.artist.name}"',
                why=f"{why.headline} ({why.rank_shift})",
            )
        )
    return tracks


def to_plaintext(tracks: Sequence[PlaylistTrack]) -> str:
    """A copy-pasteable numbered list: ``1. Artist — why``."""
    return "\n".join(
        f"{t.rank or i + 1}. {t.artist_name}" + (f" — {t.why}" if t.why else "")
        for i, t in enumerate(tracks)
    )


def to_csv(tracks: Sequence[PlaylistTrack]) -> str:
    """A spreadsheet-friendly CSV with rank, artist, search query, and why."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["rank", "artist", "artist_id", "search_query", "why"])
    for i, t in enumerate(tracks):
        writer.writerow([t.rank or i + 1, t.artist_name, t.artist_id, t.query, t.why])
    return buffer.getvalue()


def to_m3u(tracks: Sequence[PlaylistTrack], playlist_name: str = "Lavender Rotation") -> str:
    """An extended-M3U playlist. Artist entries with the 'why' as the track title.

    Most players accept a search/URL-less ``#EXTINF`` entry; this stays useful as
    a human-readable, importable artifact even without resolved track URLs.
    """
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist_name}"]
    for t in tracks:
        title = t.title or t.artist_name
        lines.append(f"#EXTINF:-1,{t.artist_name} - {title}")
        if t.why:
            lines.append(f"# why: {t.why}")
        # No resolved local/remote path offline; the artist name is the locator.
        lines.append(t.provider_url or t.artist_name)
    return "\n".join(lines) + "\n"


def to_jspf(tracks: Sequence[PlaylistTrack], playlist_name: str = "Lavender Rotation") -> str:
    """A JSPF (JSON playlist) document — structured, tool-friendly, portable."""
    playlist = {
        "playlist": {
            "title": playlist_name,
            "creator": "lavender-rotation",
            "track": [
                {
                    "creator": t.artist_name,
                    "title": t.title or "",
                    "identifier": t.provider_uri or t.artist_id,
                    "annotation": t.why,
                    "location": [t.provider_url] if t.provider_url else [],
                }
                for t in tracks
            ],
        }
    }
    return json.dumps(playlist, indent=2)


def render(
    tracks: Sequence[PlaylistTrack],
    fmt: ExportFormat,
    playlist_name: str = "Lavender Rotation",
) -> str:
    """Serialise a track list to the requested fallback format."""
    if fmt is ExportFormat.TEXT:
        return to_plaintext(tracks)
    if fmt is ExportFormat.CSV:
        return to_csv(tracks)
    if fmt is ExportFormat.M3U:
        return to_m3u(tracks, playlist_name)
    if fmt is ExportFormat.JSPF:
        return to_jspf(tracks, playlist_name)
    raise ValueError(f"unsupported export format: {fmt}")  # pragma: no cover - enum-exhaustive
