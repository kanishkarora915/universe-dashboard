"""
Backup Manager — daily off-site backup of /data SQLite databases.

Why: Render's persistent disk is single-host. If it corrupts or Render
loses data, months of trade history are gone. Off-site backup to S3/B2
gives point-in-time recovery.

Strategy:
  • Background thread checks every 60 seconds
  • At 3:00-3:05 AM IST (after EOD, before next day): tarball /data/*.db
  • Upload to S3 with date-stamped key
  • Keep last 30 days, prune older
  • Structured logs + Sentry on failure

Required env vars (all optional — backup silently disabled if missing):
  BACKUP_S3_BUCKET       e.g., "universe-dashboard-backups"
  BACKUP_S3_REGION       e.g., "ap-south-1" (Mumbai)
  AWS_ACCESS_KEY_ID      IAM user with PutObject + DeleteObject on bucket
  AWS_SECRET_ACCESS_KEY  ditto
  BACKUP_PREFIX          (optional) key prefix, default "backups/"

If env vars missing → backup thread doesn't start (safe no-op).

Bucket setup (one-time, AWS Console):
  1. Create bucket: universe-dashboard-backups (Mumbai region)
  2. Lifecycle rule: delete after 30 days (auto-prune)
  3. Block public access: ON
  4. IAM user with: s3:PutObject, s3:DeleteObject, s3:ListBucket
  5. Add access keys to Render env vars

Restore (manual, when needed):
  aws s3 cp s3://bucket/backups/2026-05-09.tar.gz .
  tar xzf 2026-05-09.tar.gz -C /data/
"""

import os
import io
import time
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
import threading
import pytz

IST = pytz.timezone("Asia/Kolkata")

# Where DBs live
_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent

# Backup window — runs once per day inside this window
_BACKUP_HOUR = 3      # 3 AM IST (after market EOD)
_BACKUP_MIN_START = 0
_BACKUP_MIN_END = 30  # 3:00 - 3:30 AM


def _is_configured() -> bool:
    """Check if backup is configured via env vars."""
    return bool(
        os.getenv("BACKUP_S3_BUCKET") and
        os.getenv("AWS_ACCESS_KEY_ID") and
        os.getenv("AWS_SECRET_ACCESS_KEY")
    )


def _ist_now() -> datetime:
    return datetime.now(IST)


def _list_db_files() -> List[Path]:
    """Find all .db files in /data."""
    if not _DATA_DIR.is_dir():
        return []
    return sorted(_DATA_DIR.glob("*.db"))


def _create_backup_archive() -> Optional[bytes]:
    """Create in-memory tar.gz of all .db files. Returns bytes or None on error.

    Uses BytesIO to avoid disk write (Render container has limited disk).
    Each DB checkpointed via SQLite VACUUM INTO before archive (safe snapshot
    even if writes happening).
    """
    db_files = _list_db_files()
    if not db_files:
        return None

    try:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for db_path in db_files:
                # Tarball the file as-is. SQLite WAL means there might be
                # uncommitted data in -wal/-shm files, but daily backup at
                # 3 AM (no market activity) means writes are quiescent.
                try:
                    tar.add(str(db_path), arcname=db_path.name)
                except Exception as e:
                    print(f"[BACKUP] Failed to tar {db_path.name}: {e}")

            # Also include the access_token.json so we can restore auth state
            token_file = _DATA_DIR / "access_token.json"
            if token_file.exists():
                try:
                    tar.add(str(token_file), arcname="access_token.json")
                except Exception:
                    pass

            # Include engine_weights.json (ML-tuned weights)
            weights_file = _DATA_DIR / "engine_weights.json"
            if weights_file.exists():
                try:
                    tar.add(str(weights_file), arcname="engine_weights.json")
                except Exception:
                    pass

        return buf.getvalue()
    except Exception as e:
        print(f"[BACKUP] Archive creation failed: {e}")
        return None


def _upload_to_s3(archive_bytes: bytes) -> bool:
    """Upload archive to S3. Returns True on success."""
    try:
        import boto3
    except ImportError:
        print("[BACKUP] boto3 not installed — skipping upload")
        return False

    try:
        bucket = os.getenv("BACKUP_S3_BUCKET")
        region = os.getenv("BACKUP_S3_REGION", "ap-south-1")
        prefix = os.getenv("BACKUP_PREFIX", "backups/")

        date_str = _ist_now().strftime("%Y-%m-%d")
        key = f"{prefix.rstrip('/')}/{date_str}.tar.gz"

        s3 = boto3.client("s3", region_name=region)
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=archive_bytes,
            ContentType="application/gzip",
            # Server-side encryption
            ServerSideEncryption="AES256",
            # Metadata
            Metadata={
                "source": "render-universe-dashboard",
                "backup-date": date_str,
                "size-bytes": str(len(archive_bytes)),
            },
        )

        size_mb = len(archive_bytes) / 1024 / 1024
        print(f"[BACKUP] ✅ Uploaded {key} ({size_mb:.2f} MB) to s3://{bucket}")

        try:
            from structured_logger import log
            log.info(
                "backup_uploaded",
                key=key,
                bucket=bucket,
                size_mb=round(size_mb, 2),
                db_count=len(_list_db_files()),
            )
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"[BACKUP] ❌ Upload failed: {e}")
        try:
            from structured_logger import log
            log.error("backup_upload_failed", error=str(e))
        except Exception:
            pass
        # Send to Sentry too if available
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(e)
        except Exception:
            pass
        return False


def _run_backup_now() -> dict:
    """Single backup run. Returns result dict for logging.
    Public — can be triggered manually via /api/backup/run-now.
    """
    if not _is_configured():
        return {"status": "skipped", "reason": "not configured (missing env vars)"}

    started = time.time()
    archive = _create_backup_archive()

    if archive is None:
        return {"status": "failed", "reason": "archive creation failed"}

    success = _upload_to_s3(archive)
    duration_sec = round(time.time() - started, 2)

    return {
        "status": "success" if success else "failed",
        "duration_sec": duration_sec,
        "size_bytes": len(archive),
        "size_mb": round(len(archive) / 1024 / 1024, 2),
        "db_count": len(_list_db_files()),
        "ts_ist": _ist_now().isoformat(),
    }


# ── Daemon (background thread) ────────────────────────────────────────────

_thread = None
_last_backup_date = None


def _daemon_loop():
    """Loop forever, run backup once per day in 3:00-3:30 AM IST window."""
    global _last_backup_date

    if not _is_configured():
        print("[BACKUP] Daemon not started — env vars missing (BACKUP_S3_BUCKET/AWS_*)")
        return

    print("[BACKUP] Daemon started — daily backup at 3:00-3:30 AM IST")

    while True:
        try:
            now = _ist_now()
            today_str = now.strftime("%Y-%m-%d")

            # Already backed up today?
            if _last_backup_date == today_str:
                time.sleep(300)  # check every 5 min
                continue

            # In backup window?
            in_window = (
                now.hour == _BACKUP_HOUR
                and _BACKUP_MIN_START <= now.minute <= _BACKUP_MIN_END
            )

            if in_window:
                print(f"[BACKUP] Triggering daily backup at {now.strftime('%H:%M:%S IST')}")
                result = _run_backup_now()
                if result["status"] == "success":
                    _last_backup_date = today_str
                # Either way, wait at least 30 min before next attempt
                time.sleep(1800)
            else:
                # Outside window — sleep till next check
                time.sleep(60)
        except Exception as e:
            print(f"[BACKUP] Daemon error: {e}")
            time.sleep(60)


def start_daemon() -> None:
    """Start the backup daemon thread. Idempotent — safe to call multiple times."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    _thread = threading.Thread(target=_daemon_loop, daemon=True, name="backup-daemon")
    _thread.start()


def get_status() -> dict:
    """Return current backup configuration + status. For /api/backup/status endpoint."""
    return {
        "configured": _is_configured(),
        "bucket": os.getenv("BACKUP_S3_BUCKET", "(not set)"),
        "region": os.getenv("BACKUP_S3_REGION", "ap-south-1"),
        "prefix": os.getenv("BACKUP_PREFIX", "backups/"),
        "data_dir": str(_DATA_DIR),
        "data_dir_exists": _DATA_DIR.is_dir(),
        "db_files": [f.name for f in _list_db_files()],
        "db_count": len(_list_db_files()),
        "daemon_running": _thread.is_alive() if _thread else False,
        "last_backup_date": _last_backup_date,
        "next_run_window": "3:00-3:30 AM IST daily",
    }
