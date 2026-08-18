"""Public USDA PubAg discovery through its official Primo endpoint."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.updates.aureli import AureliClient, _first, _mapping
from app.updates.base import OfficialBibliographicClient
from app.updates.models import BibliographicRecord, clean_text

PUBAG_DISCOVERY_URL = "https://search.nal.usda.gov/discovery/fulldisplay"
PUBAG_VIEW = "01NAL_INST:MAIN"
PUBAG_SCOPE = "pubag"
PUBAG_MAX_PAGE_SIZE = 50
PUBAG_MAX_OFFSET = 1_950


class PubAgClient(AureliClient):
    source_id = "pubag"
    source_label = "USDA PubAg"
    minimum_request_delay_seconds = 1.0

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Do not call AureliClient.__init__: its optional bearer token must never
        # be sent to a different institution.
        OfficialBibliographicClient.__init__(self, settings, transport=transport)
        self._http.headers.update(
            {
                "User-Agent": "CiderScholar/0.2 (local scientific corpus curation)",
                "Referer": "https://search.nal.usda.gov/",
            }
        )

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        normalized_query = " ".join(query.split())
        if not normalized_query or any(character in normalized_query for character in "\r\n"):
            raise ValueError("PubAg query must be non-empty and single-line")
        page_size = min(max(limit, 1), PUBAG_MAX_PAGE_SIZE)
        if not 0 <= offset <= PUBAG_MAX_OFFSET:
            raise ValueError(f"PubAg offset must be between 0 and {PUBAG_MAX_OFFSET}")
        payload = self._get_json(
            self.config.pubag_base_url,
            params={
                "vid": PUBAG_VIEW,
                "scope": PUBAG_SCOPE,
                "tab": PUBAG_SCOPE,
                "q": f"any,contains,{normalized_query}",
                "lang": "en",
                "offset": offset,
                "limit": page_size,
                "sort": "rank",
                "searchInFulltextUserSelection": "true",
                "rtaLinks": "false",
            },
        )
        docs = payload.get("docs")
        if not isinstance(docs, list):
            return []
        records: list[BibliographicRecord] = []
        for item in docs:
            if not isinstance(item, dict):
                continue
            try:
                records.append(self._record(item))
            except (TypeError, ValueError):
                continue
        return records

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        record = super()._record(item)
        pnx = _mapping(item.get("pnx"))
        control = _mapping(pnx.get("control"))
        record_id = clean_text(_first(control.get("recordid")))
        if not record_id:
            raise ValueError("PubAg record has no stable identifier")
        source_parameters = {
            "docid": record_id,
            "context": clean_text(item.get("context")) or "L",
            "vid": PUBAG_VIEW,
            "lang": "en",
            "search_scope": PUBAG_SCOPE,
        }
        return record.model_copy(
            update={"url": f"{PUBAG_DISCOVERY_URL}?{urlencode(source_parameters)}"}
        )
