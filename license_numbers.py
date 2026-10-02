"""Mirrors backend/src/main/java/com/tradecraft/backend/util/LicenseNumbers.java
exactly. The identity key every dedup/suppression check uses is
(agency, license_type, license_number_normalized) — if this normalization
ever drifts from the Java version, the same license could silently get a
second row instead of being matched to its existing one."""
import re


def normalize(raw: str) -> str:
    if raw is None:
        return ""
    return re.sub(r"[\s-]", "", raw).upper()


def last4(raw: str) -> str:
    norm = normalize(raw)
    return norm if len(norm) <= 4 else norm[-4:]
