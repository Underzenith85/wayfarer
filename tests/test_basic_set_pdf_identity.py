"""A source import must authenticate artifacts before replacing reviewed ledgers."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.import_basic_set_source_ledgers import require_selected_sources


@pytest.mark.parametrize("existing_ledger", [False, True])
def test_wrong_pdf_identity_cannot_create_or_replace_a_ledger(
    tmp_path: Path, existing_ledger: bool
) -> None:
    characters = tmp_path / "characters.pdf"
    campaigns = tmp_path / "campaigns.pdf"
    characters.write_bytes(b"%PDF-1.7\nUnselected Characters artifact\n")
    campaigns.write_bytes(b"%PDF-1.7\nUnselected Campaigns artifact\n")
    output = tmp_path / "checkout/src/wayfarer/certification/basic_set_audit"
    if existing_ledger:
        output.mkdir(parents=True)
        (output / "sections.json").write_text("reviewed ledger must survive")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/import_basic_set_source_ledgers.py",
            "--characters",
            str(characters),
            "--campaigns",
            str(campaigns),
            "--root",
            str(tmp_path / "checkout"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "SHA-256 mismatch for characters-third" in result.stderr
    if existing_ledger:
        assert (output / "sections.json").read_text() == "reviewed ledger must survive"
        assert sorted(p.name for p in output.iterdir()) == ["sections.json"]
    else:
        assert not output.exists()


def test_missing_pdf_is_not_treated_as_source_review(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        require_selected_sources(tmp_path / "missing.pdf", tmp_path / "campaigns.pdf")
