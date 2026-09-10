from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from app.knowledge.loader import MAX_FILE_BYTES, lint_package, load_package
from scripts.lint_expert_knowledge import main


def test_valid_package_is_offline_and_does_not_claim_approval(
    expert_knowledge_dir, monkeypatch
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("linter must not open network connections")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = lint_package(expert_knowledge_dir)
    assert report.structurally_valid
    assert report.scientific_approval is False
    assert report.item_count == 1
    assert report.package_sha256 == load_package(expert_knowledge_dir).package_sha256


@pytest.mark.parametrize(
    "insertion,code",
    [
        ("title: secret-marker\n", "duplicate_yaml_key"),
        ("unexpected: &value harmless\nalias: *value\n", "yaml_alias_or_anchor"),
        ("unexpected: !!python/object/apply:os.system []\n", "yaml_explicit_tag"),
        ("unexpected: " + "[" * 12 + "0" + "]" * 12 + "\n", "yaml_depth_exceeded"),
        ("body: fake\n", "duplicate_body"),
    ],
)
def test_yaml_rejects_ambiguous_or_executable_features(
    expert_knowledge_dir, insertion, code
) -> None:
    path = expert_knowledge_dir / "synthetic.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("---\n", "---\n" + insertion, 1), encoding="utf-8"
    )
    report = lint_package(expert_knowledge_dir)
    assert not report.structurally_valid
    assert report.issues[0].code == code
    assert "secret-marker" not in report.model_dump_json()
    assert str(expert_knowledge_dir) not in report.model_dump_json()


@pytest.mark.parametrize(
    "scalar", ["2026-99-99", "1" * 5000], ids=["invalid_timestamp", "oversized_integer"]
)
def test_yaml_scalar_conversion_errors_are_structured(expert_knowledge_dir, scalar) -> None:
    path = expert_knowledge_dir / "synthetic.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("---\n", f"---\nunexpected: {scalar}\n", 1),
        encoding="utf-8",
    )
    report = lint_package(expert_knowledge_dir)
    assert not report.structurally_valid
    assert report.issues[0].code == "invalid_knowledge_item"
    assert scalar not in report.model_dump_json()
    assert str(expert_knowledge_dir) not in report.model_dump_json()


def test_json_integer_conversion_errors_are_structured(expert_knowledge_dir) -> None:
    scalar = "1" * 5000
    (expert_knowledge_dir / "package.json").write_text(
        '{"schema_version":' + scalar + "}", encoding="utf-8"
    )
    report = lint_package(expert_knowledge_dir)
    assert not report.structurally_valid
    assert report.issues[0].code == "invalid_manifest"
    assert scalar not in report.model_dump_json()
    assert str(expert_knowledge_dir) not in report.model_dump_json()


@pytest.mark.parametrize(
    "contents,code",
    [
        (b"\xff", "invalid_knowledge_item"),
        (b"x" * (MAX_FILE_BYTES + 1), "file_too_large"),
        (b"No frontmatter", "missing_frontmatter"),
    ],
    ids=["invalid_utf8", "oversized_file", "missing_header"],
)
def test_bad_encoding_and_size_are_rejected(expert_knowledge_dir, contents, code) -> None:
    (expert_knowledge_dir / "synthetic.md").write_bytes(contents)
    assert lint_package(expert_knowledge_dir).issues[0].code == code


def test_provenance_must_still_match_original_source(expert_knowledge_dir) -> None:
    (expert_knowledge_dir.parent / "docs" / "METHOD.md").write_text(
        "Changed method.", encoding="utf-8"
    )
    assert lint_package(expert_knowledge_dir).issues[0].code == "provenance_changed"


def test_provenance_cannot_read_private_data(expert_knowledge_dir) -> None:
    path = expert_knowledge_dir / "synthetic.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("docs/METHOD.md", "data/secrets/private.md"),
        encoding="utf-8",
    )
    assert lint_package(expert_knowledge_dir).issues[0].code == "invalid_knowledge_item"


def test_duplicate_ids_are_rejected_before_package_assembly(expert_knowledge_dir) -> None:
    (expert_knowledge_dir / "duplicate.md").write_bytes(
        (expert_knowledge_dir / "synthetic.md").read_bytes()
    )
    manifest_path = expert_knowledge_dir / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append("duplicate.md")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert lint_package(expert_knowledge_dir).issues[0].code == "duplicate_item_id"


@pytest.mark.parametrize("path", ["../outside.md", "C:/outside.md", "folder\\file.md"])
def test_manifest_cannot_escape_package_root(expert_knowledge_dir, path) -> None:
    manifest_path = expert_knowledge_dir / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [path]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert lint_package(expert_knowledge_dir).issues[0].code == "invalid_manifest"


def test_linked_files_are_rejected_before_opening_them(expert_knowledge_dir, monkeypatch) -> None:
    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path.name == "synthetic.md" or original(path)
    )
    assert lint_package(expert_knowledge_dir).issues[0].code == "linked_path"


def test_manifest_rejects_duplicate_keys_and_case_insensitive_duplicate_paths(
    expert_knowledge_dir,
) -> None:
    manifest_path = expert_knowledge_dir / "package.json"
    original = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(original.replace("{", '{"version": "9.9.9",', 1), encoding="utf-8")
    assert lint_package(expert_knowledge_dir).issues[0].code == "duplicate_json_key"
    manifest = json.loads(original)
    manifest["files"] = ["synthetic.md", "SYNTHETIC.md"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert lint_package(expert_knowledge_dir).issues[0].code == "invalid_manifest"


def test_cli_reports_deterministically_and_cannot_overwrite_sources(
    expert_knowledge_dir, capsys
) -> None:
    output = expert_knowledge_dir.parent / "data" / "exports" / "lint.json"
    assert main(["--knowledge-dir", str(expert_knowledge_dir), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["scientific_approval"] is False
    first = capsys.readouterr().out
    assert main(["--knowledge-dir", str(expert_knowledge_dir)]) == 0
    assert capsys.readouterr().out == first
    manifest = (expert_knowledge_dir / "package.json").read_bytes()
    with pytest.raises(SystemExit):
        main(
            [
                "--knowledge-dir",
                str(expert_knowledge_dir),
                "--output",
                str(expert_knowledge_dir / "package.json"),
            ]
        )
    assert (expert_knowledge_dir / "package.json").read_bytes() == manifest


def test_cli_cannot_overwrite_a_package_staged_under_exports(expert_knowledge_dir) -> None:
    source_root = expert_knowledge_dir.parent
    staged = source_root / "data" / "exports" / "candidate"
    staged.mkdir(parents=True)
    for name in ("package.json", "synthetic.md"):
        (staged / name).write_bytes((expert_knowledge_dir / name).read_bytes())
    assert lint_package(staged, source_root=source_root).structurally_valid
    original = (staged / "package.json").read_bytes()
    with pytest.raises(SystemExit):
        main(
            [
                "--knowledge-dir",
                str(staged),
                "--source-root",
                str(source_root),
                "--output",
                str(staged / "package.json"),
            ]
        )
    assert (staged / "package.json").read_bytes() == original


def test_cli_resolves_a_linked_data_directory(expert_knowledge_dir, monkeypatch) -> None:
    source_root = expert_knowledge_dir.parent
    logical_data = source_root / "data"
    physical_data = source_root / "external-data"
    original_resolve = Path.resolve

    def resolve_link(path, *args, **kwargs):
        if path.is_relative_to(logical_data):
            path = physical_data / path.relative_to(logical_data)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_link)
    assert (
        main(
            [
                "--knowledge-dir",
                str(expert_knowledge_dir),
                "--output",
                str(logical_data / "exports" / "lint.json"),
            ]
        )
        == 0
    )
    report = json.loads((physical_data / "exports" / "lint.json").read_text(encoding="utf-8"))
    assert report["structurally_valid"]


def test_missing_package_is_a_structured_failure(tmp_path) -> None:
    assert lint_package(tmp_path / "absent").issues[0].code == "file_unavailable"
