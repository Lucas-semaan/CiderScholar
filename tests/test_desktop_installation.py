from pathlib import Path

import pytest

from app.config import load_settings
from app.desktop.layout import create_desktop_layout, desktop_paths
from app.desktop.model_integrity import (
    ModelIntegrityError,
    verify_model_manifest,
    write_model_manifest,
)
from app.desktop.system_checks import (
    DesktopCompatibilityError,
    WindowsHost,
    check_disk_capacity,
    required_installation_bytes,
    validate_windows_11_x64,
)


def test_model_manifest_detects_changes_and_extra_files(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"model":"e5"}', encoding="utf-8")
    weights = model / "model.safetensors"
    weights.write_bytes(b"safe-weights")

    write_model_manifest(model, "intfloat/multilingual-e5-base")
    manifest = verify_model_manifest(model, "intfloat/multilingual-e5-base")
    assert manifest.total_bytes == len(b'{"model":"e5"}') + len(b"safe-weights")

    weights.write_bytes(b"altered")
    with pytest.raises(ModelIntegrityError, match="hash mismatch"):
        verify_model_manifest(model)

    weights.write_bytes(b"safe-weights")
    (model / "unexpected.bin").write_bytes(b"extra")
    with pytest.raises(ModelIntegrityError, match="file list"):
        verify_model_manifest(model)


def test_desktop_layout_keeps_persistent_scopes_outside_program_files(tmp_path: Path) -> None:
    paths = desktop_paths({"LOCALAPPDATA": str(tmp_path / "Local")})
    create_desktop_layout(paths)

    assert paths.config.parent == paths.root
    for scope in ("common", "private", "queue", "exports", "backups", "secrets"):
        assert (paths.data / scope).is_dir()


def test_config_path_environment_uses_user_data_as_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "UserData" / "config.yaml"
    config.parent.mkdir()
    config.write_text("paths:\n  data_dir: data\n", encoding="utf-8")
    monkeypatch.setenv("CIDERSCHOLAR_CONFIG_PATH", str(config))

    settings = load_settings()

    assert settings.paths.data_dir == (config.parent / "data").resolve()


def test_default_config_prefers_existing_desktop_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "Local"
    config = local_app_data / "CiderScholar" / "UserData" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("paths:\n  data_dir: data\n", encoding="utf-8")
    monkeypatch.delenv("CIDERSCHOLAR_CONFIG_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    settings = load_settings()

    assert settings.paths.data_dir == (config.parent / "data").resolve()


@pytest.mark.parametrize(
    ("host", "message"),
    [
        (WindowsHost("Linux", "x86_64", 0), "Windows 11"),
        (WindowsHost("Windows", "ARM64", 22631), "64-bit x86"),
        (WindowsHost("Windows", "AMD64", 19045), "build 22000"),
    ],
)
def test_incompatible_hosts_fail_before_copy(host: WindowsHost, message: str) -> None:
    with pytest.raises(DesktopCompatibilityError, match=message):
        validate_windows_11_x64(host)
    assert validate_windows_11_x64(WindowsHost("Windows", "AMD64", 22631)) == WindowsHost(
        "Windows", "AMD64", 22631
    )


def test_disk_requirement_includes_every_payload_and_margin(tmp_path: Path) -> None:
    required = required_installation_bytes(100, 200, 300)
    assert required > 600
    assert check_disk_capacity(tmp_path, required, free_bytes=required).sufficient
    with pytest.raises(DesktopCompatibilityError, match="Espace disque"):
        check_disk_capacity(tmp_path, required, free_bytes=required - 1)
