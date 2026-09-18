"""Importing a listening history from a file: four shapes, and what each refuses.

The only route to a real profile used to be a live Last.fm key. This covers the
route that needs neither a key nor a network, and — more importantly — the two
ways a file importer goes wrong: reading a file under the wrong contract, and
quietly keeping the rows it happened to understand.

The suite-wide socket guard in `tests/conftest.py` is doing real work in this
module: every test here runs the importer for real, so "no network" is asserted
by the run rather than by reading the code.
"""

from __future__ import annotations

import json

import pytest
from pipeline.cache import Cache
from pipeline.cli import main
from pipeline.fileingest import FileIngestError, ImportedHistory, dedupe, read_history
from pipeline.ingest import build_profile, profile_from_cache

# Three artists, hand-counted: alpha 3 plays, beta 2, gamma 1.
_PLAYS = [
    ("Alpha Act", "one", 1_700_000_100),
    ("Alpha Act", "two", 1_700_000_200),
    ("Alpha Act", "three", 1_700_000_300),
    ("Beta Band", "four", 1_700_000_400),
    ("Beta Band", "five", 1_700_000_500),
    ("Gamma Group", "six", 1_700_000_600),
]
_EXPECTED_ORDER = ["Alpha Act", "Beta Band", "Gamma Group"]


def _lastfm_csv(tmp_path):
    path = tmp_path / "lastfm.csv"
    lines = ["artist,album,track,uts"]
    lines += [f"{artist},an album,{track},{ts}" for artist, track, ts in _PLAYS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _plain_csv(tmp_path):
    path = tmp_path / "plain.csv"
    lines = ["artist,track,timestamp"]
    lines += [f"{artist},{track},{ts}" for artist, track, ts in _PLAYS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _lastfm_json(tmp_path):
    path = tmp_path / "lastfm.json"
    payload = {
        "recenttracks": {
            "track": [
                {
                    "artist": {"#text": artist, "mbid": ""},
                    "name": track,
                    "date": {"uts": str(ts)},
                }
                for artist, track, ts in _PLAYS
            ]
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _spotify_json(tmp_path):
    path = tmp_path / "spotify.json"
    payload = [
        {
            "ts": f"2023-11-14T{i + 10:02d}:00:00Z",
            "master_metadata_album_artist_name": artist,
            "master_metadata_track_name": track,
        }
        for i, (artist, track, _ts) in enumerate(_PLAYS)
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _listenbrainz_json(tmp_path):
    path = tmp_path / "listenbrainz.json"
    payload = [
        {
            "listened_at": ts,
            "track_metadata": {"artist_name": artist, "track_name": track},
        }
        for artist, track, ts in _PLAYS
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


_BUILDERS = {
    "lastfm-csv": _lastfm_csv,
    "csv": _plain_csv,
    "lastfm-json": _lastfm_json,
    "spotify": _spotify_json,
    "listenbrainz": _listenbrainz_json,
}


@pytest.mark.parametrize("fmt", sorted(_BUILDERS))
def test_every_format_produces_the_hand_counted_top_artist_order(tmp_path, fmt) -> None:
    """Done-when 1, against a play count computed by hand rather than by the code."""
    path = _BUILDERS[fmt](tmp_path)
    result = read_history(path, fmt=fmt)

    assert result.rows_skipped == 0
    assert len(result.scrobbles) == len(_PLAYS)
    profile = build_profile("importer", list(result.scrobbles))
    assert profile.top_artists(3) == _EXPECTED_ORDER
    assert profile.play_counts["Alpha Act"] == 3
    assert profile.play_counts["Beta Band"] == 2
    assert profile.play_counts["Gamma Group"] == 1


@pytest.mark.parametrize("fmt", sorted(_BUILDERS))
def test_auto_detection_picks_the_same_format(tmp_path, fmt) -> None:
    """`auto` must reach the same answer, or it is importing under a wrong contract."""
    path = _BUILDERS[fmt](tmp_path)
    assert read_history(path, fmt="auto").fmt == fmt


def test_a_missing_required_column_fails_and_names_the_column(tmp_path) -> None:
    """Done-when 2a: a whole-file shape error is not a skipped row."""
    path = tmp_path / "no-track.csv"
    path.write_text("artist,timestamp\nAlpha Act,1700000100\n", encoding="utf-8")

    with pytest.raises(FileIngestError) as excinfo:
        read_history(path, fmt="csv")

    assert "'track'" in str(excinfo.value)
    assert path.name in str(excinfo.value)


def test_three_malformed_rows_are_counted_and_the_rest_imported(tmp_path) -> None:
    """Done-when 2b: the good rows land, and the file says what it lost."""
    path = tmp_path / "ragged.csv"
    path.write_text(
        "artist,track,timestamp\n"
        "Alpha Act,one,1700000100\n"
        ",orphan,1700000200\n"  # no artist
        "Beta Band,,1700000300\n"  # no track
        "Beta Band,five,not-a-time\n"  # unreadable timestamp
        "Gamma Group,six,1700000600\n",
        encoding="utf-8",
    )

    result = read_history(path, fmt="csv")

    assert result.rows_read == 5
    assert len(result.scrobbles) == 2
    assert result.rows_skipped == 3
    assert result.skipped == {
        "missing artist": 1,
        "missing track": 1,
        "unreadable timestamp": 1,
    }
    assert "3 rows skipped" in result.summary_line()


def test_a_partly_read_file_never_reports_a_clean_read(tmp_path) -> None:
    """The failure this module exists to refuse, asserted on the sentence itself."""
    path = tmp_path / "ragged.csv"
    path.write_text("artist,track,timestamp\nAlpha Act,one,1700000100\n,x,1700000200\n", "utf-8")

    summary = read_history(path, fmt="csv").summary_line()

    assert "skipped" in summary, "a summary that omits the losses reads as a complete import"


def test_an_unreadable_timestamp_is_never_replaced_with_now(tmp_path) -> None:
    """Substituting a default time would invent listening that did not happen."""
    path = tmp_path / "no-time.csv"
    path.write_text("artist,track,timestamp\nAlpha Act,one,\n", encoding="utf-8")

    result = read_history(path, fmt="csv")

    assert result.scrobbles == ()
    assert result.skipped == {"unreadable timestamp": 1}


def test_auto_refuses_to_guess_rather_than_importing_almost_nothing(tmp_path) -> None:
    """A wrong guess imports a file under the wrong contract and calls it a success."""
    path = tmp_path / "mystery.csv"
    path.write_text("who,what,when\nAlpha Act,one,1700000100\n", encoding="utf-8")

    with pytest.raises(FileIngestError) as excinfo:
        read_history(path, fmt="auto")

    assert "no format matched" in str(excinfo.value)


def test_a_listenbrainz_mbid_becomes_the_artist_key(tmp_path) -> None:
    """The same rule `parse_recent_tracks` applies: MBID when present, else the name."""
    path = tmp_path / "lb.json"
    path.write_text(
        json.dumps(
            [
                {
                    "listened_at": 1_700_000_100,
                    "track_metadata": {
                        "artist_name": "Alpha Act",
                        "track_name": "one",
                        "mbid_mapping": {"artist_mbids": ["11111111-2222-3333-4444-555555555555"]},
                    },
                },
                {
                    "listened_at": 1_700_000_200,
                    "track_metadata": {"artist_name": "Beta Band", "track_name": "two"},
                },
            ]
        ),
        encoding="utf-8",
    )

    keys = [s.artist_id for s in read_history(path, fmt="listenbrainz").scrobbles]

    assert keys == ["11111111-2222-3333-4444-555555555555", "Beta Band"]


def test_an_object_wrapped_listenbrainz_export_is_read(tmp_path) -> None:
    path = tmp_path / "lb-object.json"
    path.write_text(
        json.dumps(
            {
                "listens": [
                    {
                        "listened_at": 1_700_000_100,
                        "track_metadata": {"artist_name": "Alpha Act", "track_name": "one"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert len(read_history(path, fmt="listenbrainz").scrobbles) == 1


def test_dedupe_reports_what_a_re_import_will_actually_add(tmp_path) -> None:
    path = tmp_path / "dupes.csv"
    path.write_text(
        "artist,track,timestamp\n"
        "Alpha Act,one,1700000100\n"
        "Alpha Act,one,1700000100\n"
        "Alpha Act,two,1700000200\n",
        encoding="utf-8",
    )

    scrobbles = read_history(path, fmt="csv").scrobbles

    assert len(scrobbles) == 3
    assert len(dedupe(scrobbles)) == 2


def test_an_imported_history_offers_no_tags_and_no_similarity_graph() -> None:
    """It carries plays. Filling the other two from track names would be inference."""
    history = ImportedHistory("importer", [])

    assert history.artist_tags("anything") == ()
    assert history.similar_artists("anything") == []


# --- through the CLI, offline, on a real cache --------------------------------


def test_the_cli_import_opens_no_socket_and_leaves_every_artist_unknown(tmp_path, capsys) -> None:
    """Done-when 3, enforced by the suite's socket guard rather than by inspection."""
    path = _plain_csv(tmp_path)
    db = tmp_path / "cache.db"

    exit_code = main(["ingest", "--from-file", str(path), "--user", "importer", "--db", str(db)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "identity was not resolved" in out
    with Cache(str(db)) as cache:
        profile = profile_from_cache(cache, "importer")
        assert profile.top_artists(3) == _EXPECTED_ORDER
        assert cache.list_artist_ids() == [], "no identity may be resolved without --enrich"
        for artist_id in profile.play_counts:
            assert cache.get_artist(artist_id) is None


def test_re_running_the_same_import_adds_no_rows(tmp_path) -> None:
    """Done-when 4: idempotent on the cache's own dedupe key."""
    path = _plain_csv(tmp_path)
    db = tmp_path / "cache.db"
    argv = ["ingest", "--from-file", str(path), "--user", "importer", "--db", str(db)]

    assert main(argv) == 0
    with Cache(str(db)) as cache:
        first = len(cache.get_scrobbles("importer"))

    assert main(argv) == 0
    with Cache(str(db)) as cache:
        second = len(cache.get_scrobbles("importer"))

    assert first == len(_PLAYS)
    assert second == first


def test_the_cli_names_the_missing_column_and_imports_nothing(tmp_path, capsys) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("artist,timestamp\nAlpha Act,1700000100\n", encoding="utf-8")
    db = tmp_path / "cache.db"

    exit_code = main(["ingest", "--from-file", str(path), "--user", "importer", "--db", str(db)])

    assert exit_code == 2
    assert "'track'" in capsys.readouterr().err
    with Cache(str(db)) as cache:
        assert cache.get_scrobbles("importer") == []


def test_a_file_with_no_readable_plays_fails_rather_than_reporting_success(
    tmp_path, capsys
) -> None:
    """Zero imported rows is not a successful import of an empty history."""
    path = tmp_path / "all-bad.csv"
    path.write_text("artist,track,timestamp\n,one,1700000100\n", encoding="utf-8")
    db = tmp_path / "cache.db"

    exit_code = main(["ingest", "--from-file", str(path), "--user", "importer", "--db", str(db)])

    assert exit_code == 1
    assert "no readable plays" in capsys.readouterr().err


# --- the refusals: every whole-file shape error names what it wanted ----------


def test_invalid_json_is_reported_as_invalid_json(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"recenttracks": ', encoding="utf-8")

    with pytest.raises(FileIngestError, match="not valid JSON"):
        read_history(path, fmt="lastfm-json")


def test_lastfm_json_without_recenttracks_names_the_key(tmp_path) -> None:
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps({"tracks": []}), encoding="utf-8")

    with pytest.raises(FileIngestError, match="recenttracks"):
        read_history(path, fmt="lastfm-json")


def test_a_lastfm_json_page_list_is_read_as_pages(tmp_path) -> None:
    path = tmp_path / "pages.json"
    page = {
        "recenttracks": {
            "track": [
                {
                    "artist": {"#text": "Alpha Act", "mbid": ""},
                    "name": "one",
                    "date": {"uts": "1700000100"},
                }
            ]
        }
    }
    path.write_text(json.dumps([page, page]), encoding="utf-8")

    result = read_history(path, fmt="auto")

    assert result.fmt == "lastfm-json"
    assert result.rows_read == 2
    assert len(dedupe(result.scrobbles)) == 1


def test_a_now_playing_entry_is_counted_as_skipped_not_imported(tmp_path) -> None:
    """`parse_recent_tracks` drops it; the count must still say a row went missing."""
    path = tmp_path / "nowplaying.json"
    path.write_text(
        json.dumps(
            {
                "recenttracks": {
                    "track": [
                        {
                            "artist": {"#text": "Alpha Act", "mbid": ""},
                            "name": "live",
                            "@attr": {"nowplaying": "true"},
                        },
                        {
                            "artist": {"#text": "Alpha Act", "mbid": ""},
                            "name": "one",
                            "date": {"uts": "1700000100"},
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = read_history(path, fmt="lastfm-json")

    assert len(result.scrobbles) == 1
    assert result.rows_skipped == 1
    assert "skipped" in result.summary_line()


def test_a_spotify_export_that_is_not_an_array_is_refused(tmp_path) -> None:
    path = tmp_path / "spotify.json"
    path.write_text(json.dumps({"plays": []}), encoding="utf-8")

    with pytest.raises(FileIngestError, match="JSON array of plays"):
        read_history(path, fmt="spotify")


def test_a_spotify_play_missing_the_artist_key_is_a_shape_error(tmp_path) -> None:
    """A key absent from *every* play is the file being the wrong shape, not a bad row."""
    path = tmp_path / "spotify.json"
    path.write_text(json.dumps([{"ts": "2023-11-14T10:00:00Z"}]), encoding="utf-8")

    with pytest.raises(FileIngestError, match="master_metadata_album_artist_name"):
        read_history(path, fmt="spotify")


def test_a_listenbrainz_export_that_is_not_a_list_is_refused(tmp_path) -> None:
    path = tmp_path / "lb.json"
    path.write_text(json.dumps({"nope": 1}), encoding="utf-8")

    with pytest.raises(FileIngestError, match="array of listens"):
        read_history(path, fmt="listenbrainz")


def test_a_listen_missing_track_metadata_is_a_shape_error(tmp_path) -> None:
    path = tmp_path / "lb.json"
    path.write_text(json.dumps([{"listened_at": 1_700_000_100}]), encoding="utf-8")

    with pytest.raises(FileIngestError, match="track_metadata"):
        read_history(path, fmt="listenbrainz")


def test_a_non_object_entry_is_skipped_rather_than_crashing_the_file(tmp_path) -> None:
    path = tmp_path / "lb.json"
    path.write_text(
        json.dumps(
            [
                "not an object",
                {
                    "listened_at": 1_700_000_100,
                    "track_metadata": {"artist_name": "Alpha Act", "track_name": "one"},
                },
            ]
        ),
        encoding="utf-8",
    )

    result = read_history(path, fmt="listenbrainz")

    assert len(result.scrobbles) == 1
    assert result.rows_skipped == 1


def test_json_that_matches_no_known_shape_says_so(tmp_path) -> None:
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps([{"artist": "Alpha Act"}]), encoding="utf-8")

    with pytest.raises(FileIngestError, match="not a shape this reads"):
        read_history(path, fmt="auto")


def test_an_unknown_format_name_is_refused(tmp_path) -> None:
    path = _plain_csv(tmp_path)

    with pytest.raises(FileIngestError, match="unknown format"):
        read_history(path, fmt="mystery-format")


def test_a_missing_file_is_refused(tmp_path) -> None:
    with pytest.raises(FileIngestError, match="no such file"):
        read_history(tmp_path / "absent.csv")


def test_an_imported_history_answers_only_for_the_user_it_holds() -> None:
    from pipeline.models import Scrobble

    plays = [Scrobble(artist_id="Alpha Act", artist_name="Alpha Act", track="one", ts=1)]
    history = ImportedHistory("importer", plays)

    assert history.recent_scrobbles("importer") == plays
    assert history.recent_scrobbles("someone-else") == []
    assert history.scrobbles_since("importer", since_ts=0) == plays
    assert history.scrobbles_since("someone-else", since_ts=0) == []
