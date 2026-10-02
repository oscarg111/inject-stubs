# inject-stubs

A standalone Python loader that writes real state license data into
Tradecraft's `license_records` / `profile_stubs` tables — the production
version of what `DevStubSeeder.java` fakes for local dev. It talks directly
to Postgres (same tables the Spring Boot backend owns) rather than going
through the API, since no ingestion endpoint exists yet.

Everything here is additive to what's already built — see "What already
exists" below before changing any of the table-shape assumptions in `db.py`.

## Quick start

```bash
cd inject-stubs
pip install -r requirements.txt
cp .env.example .env    # fill in your LOCAL dev DB credentials first
python load_stubs.py florida --limit 10
```

That last command is a **safe, read-mostly test run**: it really talks to
your database (so duplicate-detection and constraints are genuinely
exercised) but rolls everything back at the end, including the batch record
itself. Nothing persists. You'll see a summary like:

```
DRY RUN — rolled back, nothing written  (batch 3f1a...)
  created:    0
  updated:    10
  skipped:    0  (new license found, but --create-new-stubs not set)
  suppressed: 0  (blocked by a prior removal request)

Sample rows touched:
  - EC     Blair Medina        Miami, FL           lic#0000971   status C
  ...
```

## The full pipeline — what each step does and where

| Step | Where | What it does |
|---|---|---|
| 1. Fetch | `sources/<state>.py` → `fetch_candidate_records()` | Downloads the source file(s)/API, decodes, yields rows as `CandidateRecord` objects (see `models.py`) |
| 2. Filter / normalize | same file | Keeps only in-scope occupation codes and status, formats names, parses dates, normalizes the license number (`license_numbers.py`) |
| 3. Suppression check | `db.is_suppressed()` | Skips any row matching a prior removal request — **no flag overrides this** |
| 4. Identity lookup | `db.find_license_record()` | Looks up `(agency, license_type, license_number_normalized)` — the same key the backend's own `LicenseRecordRepository` uses |
| 5a. Exists → refresh | `db.update_license_record()` + `db.refresh_stub_display_fields()` | Updates the mutable fields only; never touches a claimed or removed stub's display fields |
| 5b. New → create | `db.insert_license_record()` + `db.insert_profile_stub()` | Only runs when `--create-new-stubs` is passed |
| 6. Batch bookkeeping | `db.create_batch()` / `db.complete_batch()` | One `import_batches` row per run, with counts — this is what the admin rollback endpoint (`POST /admin/import-batches/{id}/rollback`) uses to undo a bad load |
| 7. Commit or roll back | `load_stubs.py` `run()` | `--commit` commits; otherwise the whole transaction rolls back |

## How duplicates are actually prevented

Four separate layers, from outermost to innermost:

1. **DB constraint** — `license_records` has a unique index on `(agency, license_type, license_number_normalized)` (see `LicenseRecord.java`). This is the backstop that can never be bypassed, including by a bug in this tool.
2. **Upsert, never blind-insert** — every row looks up its identity key first (step 4 above) and updates in place if found. Re-running the exact same load twice is a no-op the second time.
3. **`--create-new-stubs` gate** — off by default. A routine refresh run only updates license records that already exist; widening the stub set to new people is a separate, explicit decision each time (mirrors `ImportBatch.createNewStubs`'s own doc comment in the Java model).
4. **Suppression list** — a person who disputed or removed their claimed stub is recorded in `license_suppressions` and is permanently excluded from ever getting a new stub for that same license, across every future run, forever (unless an admin explicitly restores them).

What this tool deliberately does **not** try to dedupe:
- **The same person licensed in two different states.** Different `agency` = a different identity key = two legitimate separate stubs. Merging those would require person-matching across states, which the state-data evaluation (`../handoff/`) explicitly did not attempt — the accuracy-check step was never completed for any state, so guessing at cross-state identity would compound an unverified signal with another one.
- **Within-file near-duplicates from a messy source.** Florida's data was clean enough in practice that this wasn't needed. If a future state reports one license under multiple sub-classifications the way California's CSLB does, that state's `sources/<state>.py` module is responsible for merging those into one `CandidateRecord` with multiple `trade_labels` before it ever reaches `db.py` — see the comment on `LicenseRecord.tradeLabels` in the Java model for why.

## What already exists on the backend (read before changing anything)

This tool was built to fit an existing schema, not invent one — these are
already there, already correct, and this tool just has to respect them:

- `model/LicenseRecord.java`, `model/ProfileStub.java` — the two tables this writes to
- `model/ImportBatch.java` + `repository/ImportBatchRepository.java` — batch tracking, dry-run/create-new-stubs flags
- `model/LicenseSuppression.java` + `repository/LicenseSuppressionRepository.java` — the permanent removal-request blocklist
- `util/LicenseNumbers.java` — the normalization `license_numbers.py` mirrors
- `util/StatusLabels.java` — how `raw_status` later gets turned into "Active as of …" / "Not active as of …" / "N/A"; this tool never derives a label itself, it just has to store a `raw_status` that `isCurrentToken()` recognizes correctly
- `controller/AdminController.java` — `GET /admin/import-batches` (list runs) and `POST /admin/import-batches/{id}/rollback` (soft-delete every stub a batch created, **without** touching the suppression list — a bad load can be corrected and safely re-run, unlike a real removal)
- `service/DevStubSeeder.java` — the dev-only fake-data equivalent of what this tool does for real; good reference for the exact field conventions (e.g. `tradeLabels` strings like `"Electrical"`, `"HVAC"`)

## Recommended rollout sequence

1. **Test run against your local dev DB** (not production): `python load_stubs.py florida --limit 10`. Check the printed sample rows look right.
2. **Small real load, local dev DB**: `python load_stubs.py florida --limit 25 --create-new-stubs --commit`. Open the admin stubs page (`/admin/stubs`) against your local backend and eyeball the 25 real stubs.
3. **Spot-check accuracy**: pick a few of those 25 and check them against Florida's own live license lookup by hand — this is the "accuracy check" the state-data evaluation (`../handoff/HANDOFF.md` section 4) flagged as never having been completed for any state. Worth doing before trusting the data at scale.
4. **Point `.env` at staging/production** and repeat step 2 at a small `--limit` first, then drop `--limit` for the full load once you're confident.
5. **If something looks wrong after a production load**: `POST /admin/import-batches/{id}/rollback` with the batch ID printed by the run — soft-deletes everything that batch touched.

## Known open gaps (carried over from the evaluation, not solved by this tool)

- **Florida county names**: the full code→county table was never obtained (`../handoff/HANDOFF.md` section 9 only has a handful of examples), so `county` is left blank. `city` + state is used for `location_label` instead.
- **Accuracy check**: never completed for any state during the evaluation (CA/VA/FL's lookup UIs are reCAPTCHA-gated, TX's returned no results to scripted queries). Treat the first real batch as a sample to hand-verify, not a known-accurate load.
- **Status filter choice**: defaults to `current-active` (primary status Current **and** secondary status Active — the stricter, more defensible claim). Pass `--status-filter current` to match the looser "Current only" count instead; see `../handoff/HANDOFF.md` section 11 for exactly what moves between the two (98,636 vs 87,679 rows).

## Adding another state

Per the evaluation's own ranking, Texas is next, then Virginia. To add one:

1. Write `sources/<state>.py` with the same two module-level constants (`STATE`, `SOURCE_DESCRIPTION`) and the same `fetch_candidate_records(status_filter, limit)` generator contract as `sources/florida.py`.
2. Register it in `sources/__init__.py`'s `SOURCES` dict.
3. Reuse `license_numbers.normalize()` for the identity key — don't reimplement it.
4. Respect whatever source-specific decision was already made in `../handoff/HANDOFF.md`:
   - **Texas**: use the Socrata mirror (`data.texas.gov/resource/7358-krk7.json`) only — TDLR's own CSVs are off-limits per `robots.txt`. Gate every run on the mirror's `rowsUpdatedAt`; abort if it's more than 14 days old (section 6). TSBPE (plumbing) is a separate, ungated bulk CSV.
   - **Virginia**: tab-delimited, has a header row already (unlike Florida) — don't hardcode column positions, read the header.
   - **California**: use the "List by Classification" endpoint per class, never the master-list endpoint (it truncates every run — see `../handoff/STATUS.md` section 2). One license can appear under multiple classes; merge before yielding.
