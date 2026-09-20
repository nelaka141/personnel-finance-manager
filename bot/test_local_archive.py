"""Tests for the local-directory archive and the Drive import path.

Run with: python -m unittest discover -s bot -t . -p 'test_*.py'
"""
import json
import os
import tempfile
import unittest

from .local_archive import LocalArchive, import_into_drive

CSV = "Date,Amount\n2026-09-01,10\n"


class LocalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _write(self, relpath, text=CSV):
        path = os.path.join(self.dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_reads_the_nested_drive_layout(self):
        self._write("2026-09/transactions_2026-09.csv")
        self.assertEqual(LocalArchive(self.dir).read_month_csv("2026-09"), CSV)

    def test_reads_a_flattened_download(self):
        self._write("transactions_2026-08.csv")
        self.assertEqual(LocalArchive(self.dir).read_month_csv("2026-08"), CSV)

    def test_missing_month_is_none(self):
        self.assertIsNone(LocalArchive(self.dir).read_month_csv("2025-01"))

    def test_list_month_folders_covers_both_layouts(self):
        self._write("2026-09/transactions_2026-09.csv")
        self._write("transactions_2025-01.csv")
        self._write("notes/readme.txt", "hi")
        self.assertEqual(
            LocalArchive(self.dir).list_month_folders(), ["2025-01", "2026-09"])

    def test_an_empty_month_folder_is_not_listed(self):
        os.makedirs(os.path.join(self.dir, "2026-07"))
        self.assertEqual(LocalArchive(self.dir).list_month_folders(), [])

    def test_write_round_trip_and_sync_state_merge(self):
        local = LocalArchive(self.dir)
        local.write_month_csv("2026-09", CSV)
        self.assertEqual(local.read_month_csv("2026-09"), CSV)
        local.write_sync_state({"last_synced_date": "2026-09-16"})
        local.write_sync_state({"file_ids": {"root": "r"}})
        state = local.read_sync_state()
        self.assertEqual(state["last_synced_date"], "2026-09-16")
        self.assertEqual(state["file_ids"], {"root": "r"})

    def test_missing_directory_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            LocalArchive(os.path.join(self.dir, "nope"))


class _RecordingDrive:
    """Minimal DriveClient stand-in for import_into_drive."""

    def __init__(self, existing=None, unreadable=(), failing=()):
        self.months = dict(existing or {})
        self.unreadable = set(unreadable)
        self.failing = set(failing)
        self.writes = []

    def read_month_csv(self, month):
        if month in self.unreadable:
            raise RuntimeError("not visible to this app")
        return self.months.get(month)

    def write_month_csv(self, month, text):
        if month in self.failing:
            raise RuntimeError("write refused")
        self.months[month] = text
        self.writes.append(month)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for month in ("2025-01", "2026-09"):
            folder = os.path.join(self.dir, month)
            os.makedirs(folder)
            with open(os.path.join(folder, f"transactions_{month}.csv"), "w") as f:
                f.write(CSV)
        self.local = LocalArchive(self.dir)

    def test_uploads_every_local_month(self):
        drive = _RecordingDrive()
        report = import_into_drive(self.local, drive)
        self.assertEqual(report["uploaded"], ["2025-01", "2026-09"])
        self.assertEqual(report["failed"], {})

    def test_identical_months_are_skipped_so_rerunning_is_cheap(self):
        drive = _RecordingDrive(existing={"2025-01": CSV})
        report = import_into_drive(self.local, drive)
        self.assertEqual(report["already_identical"], ["2025-01"])
        self.assertEqual(report["uploaded"], ["2026-09"])

    def test_a_month_drive_cannot_read_is_uploaded_not_skipped(self):
        drive = _RecordingDrive(unreadable={"2025-01"})
        report = import_into_drive(self.local, drive)
        self.assertIn("2025-01", report["uploaded"])

    def test_one_failure_does_not_stop_the_rest(self):
        drive = _RecordingDrive(failing={"2025-01"})
        report = import_into_drive(self.local, drive)
        self.assertEqual(report["uploaded"], ["2026-09"])
        self.assertIn("2025-01", report["failed"])

    def test_months_can_be_limited(self):
        drive = _RecordingDrive()
        report = import_into_drive(self.local, drive, months=["2026-09"])
        self.assertEqual(report["uploaded"], ["2026-09"])
