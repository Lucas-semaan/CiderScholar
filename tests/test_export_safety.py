from __future__ import annotations

from app.llm.argo_key import ArgoKeyStore
from app.updates.cleanup import _export_archive


def test_every_current_archive_excludes_secret_and_cache_files(settings) -> None:
    sentinel = "CIDERSCHOLAR-SENTINEL-SECRET"
    secret_path = ArgoKeyStore(settings).path
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(sentinel, encoding="utf-8")
    cache_path = settings.paths.cache_dir / "sentinel.cache"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(sentinel, encoding="utf-8")

    archive_path = _export_archive(
        settings.paths.exports_dir,
        [{"original_record_id": "record-1", "title": "Safe notice"}],
    )
    archive_content = archive_path.read_text(encoding="utf-8")

    assert sentinel not in archive_content
    assert secret_path.name not in archive_content
    assert cache_path.name not in archive_content
    assert archive_path.is_relative_to(settings.paths.exports_dir)
