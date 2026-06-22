"""Tests for retention.py — age-based cleanup of downloaded media."""

import os
import time

import pytest

from retention import SECONDS_PER_DAY, prune_old_files


def _make_file(directory, name, age_days, now):
    """Create a file under `directory` whose mtime is `age_days` old relative to `now`."""
    path = directory / name
    path.write_text("x")
    mtime = now - age_days * SECONDS_PER_DAY
    os.utime(path, (mtime, mtime))
    return path


# A reference "now" so tests are deterministic regardless of wall clock.
NOW = 1_700_000_000.0


class TestPruneOldFiles:
    def test_deletes_files_older_than_retention(self, tmp_path):
        old = _make_file(tmp_path, "Old Episode [aaaaaaaaaaa].opus", age_days=40, now=NOW)
        deleted = prune_old_files(tmp_path, retention_days=30, now=NOW)
        assert deleted == ["Old Episode [aaaaaaaaaaa].opus"]
        assert not old.exists()

    def test_keeps_files_within_retention(self, tmp_path):
        recent = _make_file(tmp_path, "Fresh Episode [bbbbbbbbbbb].opus", age_days=5, now=NOW)
        deleted = prune_old_files(tmp_path, retention_days=30, now=NOW)
        assert deleted == []
        assert recent.exists()

    def test_mixed_ages(self, tmp_path):
        old = _make_file(tmp_path, "Old [ccccccccccc].opus", age_days=45, now=NOW)
        new = _make_file(tmp_path, "New [ddddddddddd].opus", age_days=2, now=NOW)
        deleted = prune_old_files(tmp_path, retention_days=30, now=NOW)
        assert deleted == ["Old [ccccccccccc].opus"]
        assert not old.exists()
        assert new.exists()

    def test_skips_state_log_even_when_old(self, tmp_path):
        """The state file has no [id].ext suffix, so it must never be pruned."""
        state = _make_file(tmp_path, ".downloaded_podcasts.log", age_days=400, now=NOW)
        deleted = prune_old_files(tmp_path, retention_days=30, now=NOW)
        assert deleted == []
        assert state.exists()

    def test_skips_non_download_artifacts(self, tmp_path):
        """Files that don't match the 'Title [id].ext' pattern are left alone."""
        stray = _make_file(tmp_path, "random_notes.txt", age_days=400, now=NOW)
        no_ext = _make_file(tmp_path, "no_brackets_here.opus", age_days=400, now=NOW)
        deleted = prune_old_files(tmp_path, retention_days=30, now=NOW)
        assert deleted == []
        assert stray.exists()
        assert no_ext.exists()

    @pytest.mark.parametrize("retention_days", [0, -1])
    def test_disabled_is_noop(self, tmp_path, retention_days):
        old = _make_file(tmp_path, "Old [eeeeeeeeeee].opus", age_days=400, now=NOW)
        deleted = prune_old_files(tmp_path, retention_days=retention_days, now=NOW)
        assert deleted == []
        assert old.exists()

    def test_boundary_just_over_cutoff_is_deleted(self, tmp_path):
        # One second older than exactly 30 days -> strictly older than cutoff -> deleted.
        old = _make_file(tmp_path, "Boundary [fffffffffff].opus", age_days=0, now=NOW)
        os.utime(old, (NOW - 30 * SECONDS_PER_DAY - 1, NOW - 30 * SECONDS_PER_DAY - 1))
        assert prune_old_files(tmp_path, retention_days=30, now=NOW) == ["Boundary [fffffffffff].opus"]

    def test_boundary_at_cutoff_is_kept(self, tmp_path):
        # Exactly at the cutoff is not strictly older, so it is retained.
        keep = _make_file(tmp_path, "Boundary [ggggggggggg].opus", age_days=0, now=NOW)
        os.utime(keep, (NOW - 30 * SECONDS_PER_DAY, NOW - 30 * SECONDS_PER_DAY))
        assert prune_old_files(tmp_path, retention_days=30, now=NOW) == []
        assert keep.exists()

    def test_empty_directory(self, tmp_path):
        assert prune_old_files(tmp_path, retention_days=30, now=NOW) == []

    def test_missing_directory_is_noop(self, tmp_path):
        assert prune_old_files(tmp_path / "does-not-exist", retention_days=30, now=NOW) == []

    def test_subdirectories_are_ignored(self, tmp_path):
        sub = tmp_path / "nested [hhhhhhhhhhh].opus"  # a dir that looks like a media name
        sub.mkdir()
        os.utime(sub, (NOW - 400 * SECONDS_PER_DAY, NOW - 400 * SECONDS_PER_DAY))
        deleted = prune_old_files(tmp_path, retention_days=30, now=NOW)
        assert deleted == []
        assert sub.exists()

    def test_defaults_to_wall_clock_when_now_omitted(self, tmp_path):
        old = _make_file(tmp_path, "Old [iiiiiiiiiii].opus", age_days=0, now=time.time())
        os.utime(old, (time.time() - 31 * SECONDS_PER_DAY, time.time() - 31 * SECONDS_PER_DAY))
        assert prune_old_files(tmp_path, retention_days=30) == ["Old [iiiiiiiiiii].opus"]
        assert not old.exists()
