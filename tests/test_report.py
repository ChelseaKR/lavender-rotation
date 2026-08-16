"""``lavender report``: a shareable, self-contained static HTML discovery report.

Reuses ``app.render.render_cards_html`` — the same renderer the a11y gate
audits via ``app/build_static.py`` — so the report carries the identical
accessibility guarantees (semantic landmarks, data-table score equivalent,
identity conveyed as text, never colour alone).
"""

from __future__ import annotations

from pathlib import Path

import pipeline.cli as cli
from app.a11y_check import check_html


def test_report_writes_html_file(tmp_path: Path) -> None:
    out = tmp_path / "r.html"
    rc = cli.main(["report", "--out", str(out)])
    assert rc == 0
    assert out.exists()


def test_report_contains_card_markup(tmp_path: Path) -> None:
    out = tmp_path / "r.html"
    cli.main(["report", "--out", str(out)])
    html = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert '<html lang="en">' in html
    assert 'class="card"' in html
    assert "Why this artist" in html
    assert "Identity:" in html
    assert "<table>" in html and "<caption>" in html
    assert "Privacy note:" in html


def test_report_default_out_is_my_discoveries_html(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["report"])
    assert rc == 0
    assert (tmp_path / "my-discoveries.html").exists()


def test_report_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "dir" / "r.html"
    rc = cli.main(["report", "--out", str(out)])
    assert rc == 0
    assert out.exists()


def test_report_respects_k_and_lens(tmp_path: Path) -> None:
    out = tmp_path / "r.html"
    cli.main(["report", "--out", str(out), "--k", "3", "--lens", "0.75"])
    html = out.read_text(encoding="utf-8")
    assert html.count('class="card"') == 3
    assert "75%" in html


def test_report_output_passes_the_a11y_gate(tmp_path: Path) -> None:
    out = tmp_path / "r.html"
    cli.main(["report", "--out", str(out)])
    html = out.read_text(encoding="utf-8")
    violations = check_html(html)
    assert violations == [], violations
