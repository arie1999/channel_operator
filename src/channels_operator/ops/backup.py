"""Daily SQLite backup. Snapshot the DB to data/backups/, keep last 30 days.

No-op if DATABASE_URL is not sqlite:/// (Postgres deployments use
the database's own backup tooling).
"""

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from channels_operator.settings import settings

logger = logging.getLogger(__name__)


def backup_db(backup_dir: Path | None = None, keep_days: int = 30) -> Path | None:
    """Snapshot the SQLite DB and prune older snapshots.

    Returns the path of the new backup, or None if no backup was made
    (non-sqlite URL, or DB file missing).
    """
    if not settings.database_url.startswith("sqlite:///"):
        logger.info("backup_db: DATABASE_URL is not sqlite, skipping")
        return None
    db_path = Path(settings.database_url[len("sqlite:///"):])
    if not db_path.exists():
        logger.warning("backup_db: db file not found at %s", db_path)
        return None
    backup_dir = backup_dir or (db_path.parent / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"channels_{timestamp}.db"
    shutil.copy2(db_path, target)
    logger.info("backup_db: wrote %s", target)
    cleanup_old_backups(backup_dir, keep_days=keep_days)
    return target


def cleanup_old_backups(backup_dir: Path, keep_days: int = 30) -> int:
    """Delete backup files older than keep_days. Returns count deleted."""
    if not backup_dir.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = 0
    for backup in backup_dir.glob("channels_*.db"):
        if datetime.fromtimestamp(backup.stat().st_mtime) < cutoff:
            try:
                backup.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("Could not delete old backup %s: %s", backup, exc)
    if deleted:
        logger.info("backup_db: pruned %d old backup(s)", deleted)
    return deleted
