"""Compatibility aliases for the pre-effort chat API.

New code must import :mod:`app.chat_effort`.  This module remains solely so
out-of-tree integrations can transition without changing the persisted values.
"""

from app.chat_effort import AnswerEffort, AnswerEffortBudget, answer_effort_budget

AnswerIntensity = AnswerEffort
AnswerIntensityBudget = AnswerEffortBudget


def answer_intensity_budget(intensity: AnswerIntensity | str) -> AnswerIntensityBudget:
    """Deprecated compatibility wrapper; use ``answer_effort_budget``."""

    return answer_effort_budget(intensity)
