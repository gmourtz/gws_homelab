# src/retention.py
"""Age-based retention for downloaded media.

Kept separate from main.py so the (file-system only) logic can be unit-tested
without pulling in the Google API client dependencies.
"""

import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

# yt-dlp writes files as "Title [VIDEOID].ext" (see the -o template in main.py).
# Only files matching this shape are eligible for retention — this deliberately
# skips the state log (e.g. ".downloaded_podcasts.log") and any stray artifacts.
_DOWNLOAD_NAME_RE = re.compile(r"\[[^\[\]]+\]\.[^.]+$")

SECONDS_PER_DAY = 86400


def prune_old_files(videos_dir, retention_days, now=None):
    """Delete downloaded media files in ``videos_dir`` older than ``retention_days``.

    Age is measured from each file's mtime. yt-dlp is invoked with ``--no-mtime``
    so mtime reflects *download* time, not the video's upload date.

    The download state is intentionally NOT modified here: callers keep the IDs
    recorded so an expired episode is not re-downloaded while it remains in the
    playlist. Returns the list of deleted filenames (sorted).

    No-op (returns ``[]``) when ``retention_days`` <= 0, so retention is opt-in.
    """
    if retention_days <= 0:
        return []

    videos_dir = Path(videos_dir)
    if not videos_dir.is_dir():
        return []

    now = time.time() if now is None else now
    cutoff = now - retention_days * SECONDS_PER_DAY
    deleted = []

    for path in sorted(videos_dir.iterdir()):
        if not path.is_file() or not _DOWNLOAD_NAME_RE.search(path.name):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(path.name)
                log.info("Retention: deleted %s (older than %d days)", path.name, retention_days)
        except OSError as e:
            log.warning("Retention: could not process %s: %s", path.name, e)

    if deleted:
        log.info("Retention: removed %d file(s) older than %d days.", len(deleted), retention_days)
    return deleted
