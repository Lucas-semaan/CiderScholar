from __future__ import annotations

import pytest

from scripts.create_demo_corpus import main


def test_help_does_not_generate_demo_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert not (tmp_path / "data").exists()
