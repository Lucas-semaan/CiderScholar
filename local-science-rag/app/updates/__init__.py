"""Opt-in official bibliographic metadata integrations."""

from app.updates.harvest import CiderPilotHarvester
from app.updates.models import BibliographicRecord, BibliographicSearchReport
from app.updates.service import BibliographicDiscoveryService

__all__ = [
    "BibliographicDiscoveryService",
    "BibliographicRecord",
    "BibliographicSearchReport",
    "CiderPilotHarvester",
]
