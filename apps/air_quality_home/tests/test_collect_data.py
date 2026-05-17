"""Tests for collect_data.py — the Awair collector."""

import datetime
import json
import sqlite3
from unittest.mock import MagicMock

import pytest
import requests

import collect_data


# --------------------------------------------------------------------------- #
# parse_devices
# --------------------------------------------------------------------------- #
class TestParseDevices:
    def test_valid_payload(self):
        raw = '[{"name":"Bedroom","hostname":"192.168.1.100","device_mac":"aa:bb"}]'
        devices = collect_data.parse_devices(raw)
        assert devices == [
            {"name": "Bedroom", "hostname": "192.168.1.100", "device_mac": "aa:bb"}
        ]

    def test_empty_list_is_allowed(self):
        assert collect_data.parse_devices("[]") == []

    def test_malformed_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            collect_data.parse_devices("[{not json}]")

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="must be a JSON array"):
            collect_data.parse_devices('{"name":"Bedroom"}')

    def test_entry_missing_hostname_raises(self):
        with pytest.raises(ValueError, match="needs 'name' and 'hostname'"):
            collect_data.parse_devices('[{"name":"Bedroom"}]')

    def test_entry_missing_name_raises(self):
        with pytest.raises(ValueError, match="needs 'name' and 'hostname'"):
            collect_data.parse_devices('[{"hostname":"192.168.1.100"}]')


# --------------------------------------------------------------------------- #
# load_devices — env-driven, exits on bad config
# --------------------------------------------------------------------------- #
class TestLoadDevices:
    def test_returns_devices(self, monkeypatch):
        monkeypatch.setenv(
            "AWAIR_DEVICES", '[{"name":"Bedroom","hostname":"192.168.1.100"}]'
        )
        assert collect_data.load_devices() == [
            {"name": "Bedroom", "hostname": "192.168.1.100"}
        ]

    def test_exits_when_unset(self, monkeypatch):
        monkeypatch.delenv("AWAIR_DEVICES", raising=False)
        with pytest.raises(SystemExit):
            collect_data.load_devices()

    def test_exits_when_empty(self, monkeypatch):
        monkeypatch.setenv("AWAIR_DEVICES", "[]")
        with pytest.raises(SystemExit):
            collect_data.load_devices()

    def test_exits_on_bad_json(self, monkeypatch):
        monkeypatch.setenv("AWAIR_DEVICES", "not json")
        with pytest.raises(SystemExit):
            collect_data.load_devices()


# --------------------------------------------------------------------------- #
# get_db_path
# --------------------------------------------------------------------------- #
class TestGetDbPath:
    def test_uses_data_dir_env(self, monkeypatch):
        monkeypatch.setenv("DATA_DIR", "/custom")
        assert collect_data.get_db_path() == "/custom/awair_data.db"

    def test_defaults_to_slash_data(self, monkeypatch):
        monkeypatch.delenv("DATA_DIR", raising=False)
        assert collect_data.get_db_path() == "/data/awair_data.db"


# --------------------------------------------------------------------------- #
# init_db — schema + index
# --------------------------------------------------------------------------- #
class TestInitDb:
    def test_creates_table_and_index(self):
        conn = collect_data.init_db(":memory:")
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            indexes = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert "air_quality" in tables
            assert "idx_air_quality_ts" in indexes
        finally:
            conn.close()

    def test_is_idempotent(self):
        # init_db on an already-initialised connection must not error
        conn = collect_data.init_db(":memory:")
        try:
            cur = conn.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS air_quality (id INTEGER PRIMARY KEY)"""
            )  # second create is a no-op
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# save_data_to_db — timestamp conversion + persistence
# --------------------------------------------------------------------------- #
class TestSaveDataToDb:
    def _row(self, conn):
        return conn.execute(
            "SELECT device_name, device_mac, timestamp, temp, co2 FROM air_quality"
        ).fetchone()

    def test_persists_reading(self):
        conn = collect_data.init_db(":memory:")
        try:
            data = {"timestamp": "2026-01-15T12:00:00.000Z", "temp": 21.5, "co2": 450}
            assert collect_data.save_data_to_db(conn, "Bedroom", "aa:bb", data) is True
            row = self._row(conn)
            assert row[0] == "Bedroom"
            assert row[1] == "aa:bb"
            assert row[3] == 21.5
            assert row[4] == 450
        finally:
            conn.close()

    def test_returns_false_on_db_error(self):
        # A connection with no air_quality table — the INSERT fails
        conn = sqlite3.connect(":memory:")
        try:
            assert (
                collect_data.save_data_to_db(conn, "Bedroom", "aa:bb", {"temp": 20.0})
                is False
            )
        finally:
            conn.close()

    def test_winter_timestamp_stays_utc_offset(self):
        # January — Europe/London is GMT (+00:00)
        conn = collect_data.init_db(":memory:")
        try:
            collect_data.save_data_to_db(
                conn, "Bedroom", "aa:bb", {"timestamp": "2026-01-15T12:00:00.000Z"}
            )
            assert self._row(conn)[2] == "2026-01-15T12:00:00.000+00:00"
        finally:
            conn.close()

    def test_summer_timestamp_shifts_to_bst(self):
        # July — Europe/London is BST (+01:00), so 12:00Z -> 13:00 local
        conn = collect_data.init_db(":memory:")
        try:
            collect_data.save_data_to_db(
                conn, "Bedroom", "aa:bb", {"timestamp": "2026-07-15T12:00:00.000Z"}
            )
            assert self._row(conn)[2] == "2026-07-15T13:00:00.000+01:00"
        finally:
            conn.close()

    def test_missing_timestamp_falls_back_to_now(self):
        conn = collect_data.init_db(":memory:")
        try:
            collect_data.save_data_to_db(conn, "Bedroom", "aa:bb", {"temp": 20.0})
            stored = self._row(conn)[2]
            # A valid ISO-8601 string was generated rather than crashing
            assert datetime.datetime.fromisoformat(stored) is not None
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Awair HTTP API
# --------------------------------------------------------------------------- #
class TestGetAwairData:
    def test_returns_json_on_success(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"temp": 21.0}
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        assert collect_data.get_awair_data(session, "host") == {"temp": 21.0}

    def test_returns_none_on_failure(self):
        session = MagicMock()
        session.get.side_effect = requests.RequestException("unreachable")
        assert collect_data.get_awair_data(session, "host") is None


class TestResolveMac:
    def test_uses_configured_mac_without_network(self):
        session = MagicMock()
        device = {"name": "Bedroom", "hostname": "h", "device_mac": "aa:bb"}
        assert collect_data.resolve_mac(session, device) == "aa:bb"
        session.get.assert_not_called()

    def test_discovers_mac_when_absent(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"device_uuid": "discovered-mac"}
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        device = {"name": "Bedroom", "hostname": "h"}
        assert collect_data.resolve_mac(session, device) == "discovered-mac"

    def test_returns_none_when_undiscoverable(self):
        session = MagicMock()
        session.get.side_effect = requests.RequestException("unreachable")
        device = {"name": "Bedroom", "hostname": "h"}
        assert collect_data.resolve_mac(session, device) is None


# --------------------------------------------------------------------------- #
# Health endpoint logic
# --------------------------------------------------------------------------- #
class TestHealthThreshold:
    def test_three_poll_cycles(self):
        assert collect_data.health_threshold(200) == 600

    def test_floor_is_300_seconds(self):
        assert collect_data.health_threshold(10) == 300


class TestComputeHealth:
    DEVICES = [{"name": "A", "hostname": "h1"}, {"name": "B", "hostname": "h2"}]
    NOW = datetime.datetime(2026, 5, 17, 12, 0, 0)

    def _ago(self, seconds):
        return self.NOW - datetime.timedelta(seconds=seconds)

    def test_all_devices_fresh_is_healthy(self):
        last = {"A": self._ago(60), "B": self._ago(120)}
        healthy, report = collect_data.compute_health(
            self.DEVICES, last, self.NOW, self.NOW, 360
        )
        assert healthy is True
        assert "STALE" not in report

    def test_one_stale_device_is_unhealthy(self):
        last = {"A": self._ago(60), "B": self._ago(999)}
        healthy, report = collect_data.compute_health(
            self.DEVICES, last, self.NOW, self.NOW, 360
        )
        assert healthy is False
        assert "B: STALE" in report
        assert "A: ok" in report

    def test_startup_grace_keeps_healthy_before_first_reading(self):
        started = self._ago(100)  # 100s ago, within the 360s grace window
        healthy, report = collect_data.compute_health(
            self.DEVICES, {}, started, self.NOW, 360
        )
        assert healthy is True
        assert "starting" in report

    def test_unhealthy_when_no_reading_past_grace(self):
        started = self._ago(400)  # past the 360s grace window
        healthy, report = collect_data.compute_health(
            self.DEVICES, {}, started, self.NOW, 360
        )
        assert healthy is False
        assert "STALE" in report

    def test_device_on_exact_threshold_is_stale(self):
        # age == threshold is not < threshold -> stale
        last = {"A": self._ago(60), "B": self._ago(360)}
        healthy, _ = collect_data.compute_health(
            self.DEVICES, last, self.NOW, self.NOW, 360
        )
        assert healthy is False
