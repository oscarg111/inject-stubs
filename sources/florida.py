"""Florida DBPR — construction (board 06) + electrical (board 08) extracts.

Confirmed compliant: handoff/HANDOFF.md section 8 rates this endpoint "YES" —
an official free public-records download, robots.txt does not block it, and
DBPR's own page says it "provides copies of electronic records to the public
through free download."

Layout (22 fields, no header, latin-1) and the occupation-code -> trade
mapping are from handoff/HANDOFF.md sections 9 and 11, confirmed against
https://www2.myfloridalicense.com/about-us/understanding-dbpr-codes/.

Known gap (see handoff/HANDOFF.md section 9): the full Florida county-code
table was never obtained, only a handful of example codes — so `county` is
left unset here rather than storing an undecoded number. `city` + state is
enough for a location_label; nothing downstream requires county.
"""
import csv
import io
import urllib.request
from datetime import date, datetime
from email.utils import parsedate_to_datetime

from license_numbers import normalize
from models import CandidateRecord

STATE = "FL"
AGENCY = "DBPR"
SOURCE_DESCRIPTION = "Florida DBPR construction + electrical extracts"

FILES = [
    ("06", "https://www2.myfloridalicense.com/sto/file_download/extracts//CONSTRUCTIONLICENSE_1.csv"),
    ("08", "https://www2.myfloridalicense.com/sto/file_download/extracts/lic08el.csv"),
]

# Certified + registered variants. Excludes (deliberately): QB/FRO/PVDR/CRS*
# (business or course records, no individual license), CCC/RC (roofing),
# CPC/RP (pool/spa), CSC/RS (sheet metal), CUC (utility/excavation), CVC
# (solar), SCC/RX (specialty, scope unclear), EF/EG/EY/EZ/EH/EI (alarm
# systems), ES/ET (specialty electrical, scope unclear per the eval).
OCCUPATION_TRADE = {
    "CGC": "General Contracting", "CBC": "General Contracting", "CRC": "General Contracting",
    "RG": "General Contracting", "RB": "General Contracting", "RR": "General Contracting",
    "CAC": "HVAC", "CMC": "HVAC", "RA": "HVAC", "RM": "HVAC",
    "CFC": "Plumbing", "RF": "Plumbing",
    "EC": "Electrical", "ER": "Electrical",
}

# Identifies this as a real, contactable loader rather than a one-off script —
# good practice for a bot that will hit a government server on a recurring
# schedule, not just once.
USER_AGENT = "Tradecraft-stub-loader/1.0 (+https://tradecraft.works; contact: oscar.gross@tradecraft.works)"


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
        last_modified = resp.headers.get("Last-Modified")
    # The file's own freshness date, not "today" — same principle as the
    # Texas mirror decision in handoff/HANDOFF.md section 6: a stub's
    # "checked as of" date should reflect the source, not the fetch.
    retrieved_at = date.today()
    if last_modified:
        try:
            retrieved_at = parsedate_to_datetime(last_modified).date()
        except (TypeError, ValueError):
            pass
    return raw, retrieved_at


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        return None


_NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def _format_name(raw):
    """"SMITH, JOHN Q JR" -> "John Q Smith Jr" (suffix moved after the
    surname, not left stuck between middle name and last name). Best-effort —
    a handful of rows with unusual punctuation will come through less tidy
    than this; nothing downstream depends on perfect parsing, it's a display
    string."""
    raw = (raw or "").strip()
    if "," not in raw:
        return raw.title()
    last, _, rest = raw.partition(",")
    tokens = rest.strip().split()
    suffix = ""
    if tokens and tokens[-1].upper().rstrip(".") in _NAME_SUFFIXES:
        token = tokens.pop().upper().rstrip(".")
        suffix = token if token in {"II", "III", "IV", "V"} else token.title()
    given = " ".join(t.title() for t in tokens)
    full = f"{given} {last.strip().title()}".strip()
    return f"{full} {suffix}".strip() if suffix else full


def fetch_candidate_records(status_filter="current-active", limit=None):
    """Yields CandidateRecord for in-scope Florida rows across both files.

    status_filter:
      "current"        -> primary status C only (HANDOFF.md sec. 11 "Primary = C" column)
      "current-active" -> primary C AND secondary A (the stricter column; default)
    limit: stop after this many yielded rows total, across both files — for a
    small test run without needing to avoid downloading the full files (the
    files themselves are flat CSV; there's no server-side filter to ask for).
    """
    yielded = 0
    for board, url in FILES:
        raw, retrieved_at = _fetch(url)
        text = raw.decode("latin-1", errors="replace")
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if limit is not None and yielded >= limit:
                return
            if len(row) != 22:
                continue  # malformed/truncated row (e.g. a cut-off final line) — skip, don't guess

            occupation_code = row[1].strip()
            trade = OCCUPATION_TRADE.get(occupation_code)
            if trade is None:
                continue

            license_number_raw = row[12].strip()
            if not license_number_raw:
                continue  # QB-style business-info row, no actual license number

            primary_status = row[13].strip()
            secondary_status = row[14].strip()
            if primary_status != "C":
                continue
            if status_filter == "current-active" and secondary_status != "A":
                continue

            city = row[8].strip().title()
            licensee_name = row[2].strip()

            yield CandidateRecord(
                agency=AGENCY,
                state=STATE,
                license_type=occupation_code,
                license_number_raw=license_number_raw,
                license_number_normalized=normalize(license_number_raw),
                holder_name=_format_name(licensee_name),
                holder_type="PERSON",
                trade_labels=[trade],
                city=city or None,
                county=None,
                raw_status=primary_status,  # "C" — matches StatusLabels.isCurrentToken()
                issued_at=_parse_date(row[15]),
                expires_at=_parse_date(row[17]),
                source_name="Florida DBPR",
                source_url=url,
                retrieved_at=retrieved_at,
                location_label=f"{city}, FL" if city else "FL",
            )
            yielded += 1
