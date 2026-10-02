#!/usr/bin/env python3
"""Load real state license data into Tradecraft's license_records /
profile_stubs tables. See README.md for the full runbook.

Safety model:
  - Every run (dry or live) executes the exact same code, including real
    duplicate-detection and constraint checks, inside one transaction.
  - Without --commit, that transaction is ROLLED BACK at the end — nothing
    persists, not even the import_batches row. This is the "test run."
  - Without --create-new-stubs, a run only refreshes license_records that
    already exist (by agency/license_type/license_number_normalized) and
    never creates a brand-new license_record or profile_stub. You have to
    opt into widening the stub set explicitly.
  - A license_suppressions match always skips the row, create or refresh,
    live run or dry run, no flag overrides this.
  - A claimed or removed profile_stub's display fields are never touched by
    a refresh — only its underlying license_record is kept current.

Examples:
  python load_stubs.py florida --limit 10
      Dry run, 10 rows, refresh-only (the default — see output for what
      WOULD happen; nothing is written).

  python load_stubs.py florida --limit 500 --create-new-stubs --commit
      Real run: create up to 500 new stubs for Florida license holders who
      don't have one yet (and refresh any that already exist).

  python load_stubs.py florida --create-new-stubs --commit
      The full production load (no --limit) once you're confident.
"""
import argparse
import getpass
import sys

import db
from sources import SOURCES


def run(source_name, args):
    source = SOURCES[source_name]
    conn = db.get_connection()
    conn.autocommit = False
    cur = conn.cursor()

    batch_id = db.create_batch(
        cur,
        state=source.STATE,
        source_file=source.SOURCE_DESCRIPTION,
        run_by=getpass.getuser(),
        dry_run=not args.commit,
        create_new_stubs=args.create_new_stubs,
    )

    created = updated = skipped = suppressed = 0
    samples = []

    try:
        for rec in source.fetch_candidate_records(status_filter=args.status_filter, limit=args.limit):
            if db.is_suppressed(cur, rec.agency, rec.license_type, rec.license_number_normalized):
                suppressed += 1
                continue

            existing_id = db.find_license_record(
                cur, rec.agency, rec.license_type, rec.license_number_normalized
            )

            if existing_id:
                db.update_license_record(cur, existing_id, rec, batch_id)
                stub = db.find_stub(cur, existing_id)
                if stub and stub["claimed_profile_id"] is None and stub["removed_at"] is None:
                    db.refresh_stub_display_fields(cur, stub["id"], rec)
                updated += 1
            else:
                if not args.create_new_stubs:
                    skipped += 1
                    continue
                new_id = db.insert_license_record(cur, rec, batch_id)
                db.insert_profile_stub(cur, new_id, rec, batch_id)
                created += 1

            if len(samples) < 10:
                samples.append(rec)

        db.complete_batch(cur, batch_id, created, updated, skipped + suppressed)

    except Exception:
        conn.rollback()
        conn.close()
        raise

    mode = "LIVE RUN - committed" if args.commit else "DRY RUN - rolled back, nothing written"
    print(f"\n{mode}  (batch {batch_id})")
    print(f"  created:    {created}")
    print(f"  updated:    {updated}")
    print(f"  skipped:    {skipped}  (new license found, but --create-new-stubs not set)")
    print(f"  suppressed: {suppressed}  (blocked by a prior removal request)")

    if samples:
        print("\nSample rows touched:")
        for r in samples:
            print(f"  - {r.license_type:6s} {r.holder_name:<28s} {r.location_label:<20s} "
                  f"lic#{r.license_number_raw:<12s} status {r.raw_status}")

    if args.commit:
        conn.commit()
        print("\nCommitted.")
    else:
        conn.rollback()
        print("\nRolled back - re-run with --commit once this looks right.")

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Load real state license data into license_records/profile_stubs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("source", choices=sorted(SOURCES.keys()))
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Without this, the run still touches the real database "
             "inside a transaction (so duplicate/constraint checks are real) but rolls "
             "back at the end.",
    )
    parser.add_argument(
        "--create-new-stubs", action="store_true",
        help="Allow creating brand-new license_records/profile_stubs. Without this, "
             "a run only refreshes records that already exist.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Stop after this many candidate rows (for a small test run).",
    )
    parser.add_argument(
        "--status-filter", choices=["current", "current-active"], default="current-active",
        help="current = primary status Current only. current-active = primary Current AND "
             "secondary Active (stricter; default). See handoff/HANDOFF.md section 11.",
    )
    args = parser.parse_args()

    if args.commit and args.limit is None and args.create_new_stubs:
        confirm = input(
            "This will create new stubs for EVERY in-scope row with no --limit, committed "
            "to the database. Type 'yes' to continue: "
        )
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(1)

    run(args.source, args)


if __name__ == "__main__":
    main()
