"""Shared shape every state source module must produce. One CandidateRecord
maps 1:1 onto a (license_records row + its trade labels + the profile_stubs
row it backs) — see db.py for exactly how."""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional


@dataclass
class CandidateRecord:
    agency: str
    state: str
    license_type: str
    license_number_raw: str
    license_number_normalized: str
    holder_name: str
    holder_type: str  # "PERSON" or "COMPANY" — must match Enumerations.SubjectType
    trade_labels: List[str]
    city: Optional[str]
    county: Optional[str]
    raw_status: Optional[str]  # source's own token — must still satisfy
    # StatusLabels.isCurrentToken() if it means "current" (CURRENT/ACTIVE/CLEAR/C/A),
    # since nothing downstream re-derives this; see util/StatusLabels.java.
    issued_at: Optional[date]
    expires_at: Optional[date]
    source_name: str
    source_url: str
    retrieved_at: date  # the SOURCE's freshness date, not today — see Texas
    # precedent in handoff/HANDOFF.md section 6.
    location_label: str  # "City, ST" or "County, ST" — whatever the source gave
