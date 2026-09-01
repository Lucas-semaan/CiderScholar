"""Allocate a bounded generation evidence budget across required research axes."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence


def select_records_with_axis_coverage[Record](
    records: Sequence[Record],
    *,
    axis_candidate_ids: Mapping[str, Sequence[str]] | None,
    record_id: Callable[[Record], str],
    record_limit: int,
) -> list[Record]:
    """Reserve one distinct ranked record for every documented required axis.

    The input order is the retrieval ranking.  An augmenting-path assignment
    maximizes the number of axes represented by distinct records, so an early
    multi-axis record cannot crowd out a later record that is the only support
    for another axis.  Remaining places retain the original global ranking.
    """

    if record_limit < 1:
        raise ValueError("record_limit must be positive")
    if not records:
        return []

    ranked_ids = [record_id(item) for item in records]
    index_by_id = {identifier: index for index, identifier in enumerate(ranked_ids)}
    axis_candidates = [
        [index_by_id[identifier] for identifier in candidate_ids if identifier in index_by_id]
        for axis_key, candidate_ids in (axis_candidate_ids or {}).items()
        if axis_key.strip()
    ]

    # A standard augmenting-path matching assigns a distinct record to as many
    # axes as possible. Candidate order inherits retrieval rank.
    matched_axis_by_record: dict[int, int] = {}

    def assign(axis_index: int, seen_records: set[int]) -> bool:
        for candidate_index in axis_candidates[axis_index]:
            if candidate_index in seen_records:
                continue
            seen_records.add(candidate_index)
            previous_axis = matched_axis_by_record.get(candidate_index)
            if previous_axis is None or assign(previous_axis, seen_records):
                matched_axis_by_record[candidate_index] = axis_index
                return True
        return False

    for axis_index in range(len(axis_candidates)):
        assign(axis_index, set())

    reserved_indices = sorted(matched_axis_by_record)
    selected_indices = reserved_indices[:record_limit]
    selected_index_set = set(selected_indices)
    for index in range(len(records)):
        if len(selected_indices) >= record_limit:
            break
        if index not in selected_index_set:
            selected_indices.append(index)
            selected_index_set.add(index)
    return [records[index] for index in selected_indices]
