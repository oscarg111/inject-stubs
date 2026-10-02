from . import florida

# Add a new state by writing sources/<state>.py with the same two names
# (STATE, SOURCE_DESCRIPTION) and the same fetch_candidate_records(status_filter,
# limit) generator contract (see florida.py), then register it here.
SOURCES = {
    "florida": florida,
}
