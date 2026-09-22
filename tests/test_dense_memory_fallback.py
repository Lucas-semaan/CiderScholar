from app.memory import MemorySnapshot
from app.services.workflows import _dense_chat_retrieval_is_safe


def test_dense_chat_retrieval_requires_a_real_desktop_memory_margin(settings, monkeypatch):
    monkeypatch.setattr(
        "app.services.workflows.MemoryGuard.snapshot",
        lambda _guard: MemorySnapshot(
            process_rss_gb=0.2,
            system_used_gb=7.5,
            system_available_gb=3.99,
        ),
    )

    assert _dense_chat_retrieval_is_safe(settings) is False


def test_dense_chat_retrieval_is_allowed_with_the_required_memory_margin(settings, monkeypatch):
    monkeypatch.setattr(
        "app.services.workflows.MemoryGuard.snapshot",
        lambda _guard: MemorySnapshot(
            process_rss_gb=0.2,
            system_used_gb=4.5,
            system_available_gb=4.0,
        ),
    )

    assert _dense_chat_retrieval_is_safe(settings) is True
