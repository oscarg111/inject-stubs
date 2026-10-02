"""Thin data-access layer over the same tables the Java backend owns
(license_records, profile_stubs, import_batches, license_suppressions,
license_record_trade_labels). Schema is Hibernate-managed (ddl-auto=update)
on the Java side — this module never creates or alters tables, only reads
and writes rows that already fit the live schema.

Every write in here goes through load_stubs.py's single transaction, so a
dry run can roll the whole thing back at the end while still exercising the
real duplicate-detection and constraint behavior.
"""
import os
import uuid
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_ENDPOINT_WRITER"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "tradecraft_database"),
        user=os.environ["DB_USERNAME_WRITER"],
        password=os.environ["DB_PASSWORD_WRITER"],
    )


# ── import_batches ───────────────────────────────────────────────────────────

def create_batch(cur, state, source_file, run_by, dry_run, create_new_stubs):
    batch_id = uuid.uuid4()
    cur.execute(
        """INSERT INTO import_batches
           (id, state, source_file, run_by, dry_run, create_new_stubs, started_at,
            created_count, updated_count, skipped_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, 0)""",
        (str(batch_id), state, source_file, run_by, dry_run, create_new_stubs,
         datetime.now(timezone.utc)),
    )
    return batch_id


def complete_batch(cur, batch_id, created, updated, skipped):
    cur.execute(
        """UPDATE import_batches
           SET completed_at = %s, created_count = %s, updated_count = %s, skipped_count = %s
           WHERE id = %s""",
        (datetime.now(timezone.utc), created, updated, skipped, str(batch_id)),
    )


# ── license_suppressions ─────────────────────────────────────────────────────
# A row here means "this exact license must never get a stub again" — written
# when someone disputes/removes a claimed stub. We check it before every
# create AND every refresh; a batch rollback (AdminController) deliberately
# does NOT write here, so only a real removal request ever blocks a reload.

def is_suppressed(cur, agency, license_type, license_number_normalized):
    cur.execute(
        """SELECT 1 FROM license_suppressions
           WHERE agency = %s AND license_type = %s AND license_number_normalized = %s""",
        (agency, license_type, license_number_normalized),
    )
    return cur.fetchone() is not None


# ── license_records ──────────────────────────────────────────────────────────

def find_license_record(cur, agency, license_type, license_number_normalized):
    """Returns the existing row's id, or None — the exact same lookup key as
    LicenseRecordRepository.findByAgencyAndLicenseTypeAndLicenseNumberNormalized."""
    cur.execute(
        """SELECT id FROM license_records
           WHERE agency = %s AND license_type = %s AND license_number_normalized = %s""",
        (agency, license_type, license_number_normalized),
    )
    row = cur.fetchone()
    return row[0] if row else None


def insert_license_record(cur, rec, batch_id):
    record_id = uuid.uuid4()
    cur.execute(
        """INSERT INTO license_records
           (id, batch_id, agency, state, license_type, license_number_raw, license_number_normalized,
            holder_name, holder_type, city, county, raw_status, issued_at, expires_at,
            source_name, source_url, retrieved_at, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(record_id), str(batch_id), rec.agency, rec.state, rec.license_type,
         rec.license_number_raw, rec.license_number_normalized, rec.holder_name, rec.holder_type,
         rec.city, rec.county, rec.raw_status, rec.issued_at, rec.expires_at,
         rec.source_name, rec.source_url, rec.retrieved_at, datetime.now(timezone.utc)),
    )
    _set_trade_labels(cur, record_id, rec.trade_labels)
    return record_id


def update_license_record(cur, record_id, rec, batch_id):
    """A refresh never touches license_number_raw/normalized, agency, state or
    license_type — that's the identity key that got us here; only the mutable
    descriptive fields move."""
    cur.execute(
        """UPDATE license_records SET
            batch_id=%s, holder_name=%s, holder_type=%s, city=%s, county=%s, raw_status=%s,
            issued_at=%s, expires_at=%s, source_name=%s, source_url=%s, retrieved_at=%s
           WHERE id = %s""",
        (str(batch_id), rec.holder_name, rec.holder_type, rec.city, rec.county, rec.raw_status,
         rec.issued_at, rec.expires_at, rec.source_name, rec.source_url, rec.retrieved_at,
         str(record_id)),
    )
    _set_trade_labels(cur, record_id, rec.trade_labels)


def _set_trade_labels(cur, record_id, trade_labels):
    cur.execute("DELETE FROM license_record_trade_labels WHERE license_record_id = %s", (str(record_id),))
    for label in trade_labels:
        cur.execute(
            "INSERT INTO license_record_trade_labels (license_record_id, trade_label) VALUES (%s, %s)",
            (str(record_id), label),
        )


# ── profile_stubs ─────────────────────────────────────────────────────────────

def find_stub(cur, license_record_id):
    cur.execute(
        """SELECT id, claimed_profile_id, removed_at FROM profile_stubs
           WHERE license_record_id = %s""",
        (str(license_record_id),),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "claimed_profile_id": row[1], "removed_at": row[2]}


def insert_profile_stub(cur, license_record_id, rec, batch_id):
    stub_id = uuid.uuid4()
    cur.execute(
        """INSERT INTO profile_stubs
           (id, license_record_id, batch_id, subject_type, display_name, location_label,
            noindex, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(stub_id), str(license_record_id), str(batch_id), rec.holder_type, rec.holder_name,
         rec.location_label, False, datetime.now(timezone.utc)),
    )
    return stub_id


def refresh_stub_display_fields(cur, stub_id, rec):
    """Only called for a stub that is neither claimed nor removed — see the
    guard in load_stubs.py. A claimed stub's display fields belong to the
    real account now, not the source data; a removed one is hidden on
    purpose and a refresh must not quietly bring it back."""
    cur.execute(
        "UPDATE profile_stubs SET display_name = %s, location_label = %s WHERE id = %s",
        (rec.holder_name, rec.location_label, str(stub_id)),
    )
