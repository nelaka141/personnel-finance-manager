"""Tests for the drive.file-scoped Drive client.

FakeDrive models the two behaviours that broke the 2026-09-19 run: files
belonging to another app are invisible to `files.list` but still occupy
their name in the folder, and a folder can hold several files with the same
name. Run with: python -m unittest discover -s bot -t . -p 'test_*.py'
"""
import json
import os
import unittest
from unittest import mock

from googleapiclient.errors import HttpError

from . import drive_client as dc


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "test"


def _http_error(status):
    return HttpError(_Resp(status), b"{}")


class _Call:
    """Mimics googleapiclient's request objects: build now, execute later."""

    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeFiles:
    def __init__(self, drive):
        self.drive = drive

    def list(self, q=None, fields=None, orderBy=None, pageSize=None, **kwargs):
        return _Call(lambda: self.drive._list(q))

    def get(self, fileId=None, fields=None, **kwargs):
        return _Call(lambda: self.drive._get(fileId))

    def get_media(self, fileId=None, **kwargs):
        return _Call(lambda: self.drive._media(fileId))

    def create(self, body=None, media_body=None, fields=None, **kwargs):
        return _Call(lambda: self.drive._create(body, media_body))

    def update(self, fileId=None, media_body=None, fields=None, **kwargs):
        return _Call(lambda: self.drive._update(fileId, media_body))


class FakeDrive:
    """In-memory Drive with per-file app visibility."""

    def __init__(self):
        self.nodes = {}  # id -> dict(name, mime, parent, content, visible)
        self._next = 0
        self.created_ids = []
        self.updated_ids = []

    def files(self):
        return FakeFiles(self)

    # -- fixture helpers ---------------------------------------------------

    def add(self, name, mime="text/csv", parent=None, content="", visible=True,
            modified="2026-01-01T00:00:00Z"):
        self._next += 1
        fid = f"id{self._next}"
        self.nodes[fid] = {
            "id": fid, "name": name, "mimeType": mime, "parent": parent,
            "content": content, "visible": visible, "modifiedTime": modified,
        }
        return fid

    def add_folder(self, name, parent=None, visible=True, modified="2026-01-01T00:00:00Z"):
        return self.add(name, dc.FOLDER_MIME, parent, "", visible, modified)

    # -- API surface -------------------------------------------------------

    def _visible(self, fid):
        node = self.nodes.get(fid)
        if node is None or not node["visible"]:
            raise _http_error(404)
        return node

    def _get(self, fid):
        node = self._visible(fid)
        return {k: node[k] for k in ("id", "name", "mimeType", "modifiedTime")}

    def _media(self, fid):
        return self._visible(fid)["content"].encode("utf-8")

    def _list(self, q):
        # Only the query shapes this module actually builds are parsed.
        name = _quoted_after(q, "name = ")
        mime = _quoted_after(q, "mimeType = ")
        parent = _quoted_after(q, "", suffix=" in parents")
        out = []
        for node in self.nodes.values():
            if not node["visible"]:
                continue
            if parent is not None and node["parent"] != parent:
                continue
            if name is not None and node["name"] != name:
                continue
            if mime is not None and node["mimeType"] != mime:
                continue
            out.append({k: node[k] for k in ("id", "name", "mimeType", "modifiedTime")})
        out.sort(key=lambda f: f["modifiedTime"], reverse=True)
        return {"files": out}

    def _create(self, body, media_body):
        parents = body.get("parents") or [None]
        content = media_body.content if media_body is not None else ""
        fid = self.add(body["name"], body.get("mimeType", "text/csv"), parents[0], content)
        self.created_ids.append(fid)
        return {"id": fid}

    def _update(self, fid, media_body):
        node = self._visible(fid)
        if media_body is not None:
            node["content"] = media_body.content
        self.updated_ids.append(fid)
        return {"id": fid}


def _quoted_after(text, prefix, suffix=None):
    """Read the single-quoted value that follows `prefix`, unescaping it.

    Drive unescapes \\' and \\\\ in a `q` string, so the fake has to as well --
    otherwise a name containing an apostrophe would look like a parser bug
    here instead of exercising drive_client._escape.
    """
    if not text:
        return None
    start = 0
    if prefix:
        if prefix not in text:
            return None
        start = text.index(prefix) + len(prefix)
    if start >= len(text) or text[start] != "'":
        return None
    i = start + 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "'":
            break
        out.append(ch)
        i += 1
    else:
        return None
    if suffix is not None and not text[i + 1:].startswith(suffix):
        return None
    return "".join(out)


class _FakeMedia:
    """Stand-in for MediaIoBaseUpload that keeps the text readable."""

    def __init__(self, fh, mimetype=None, resumable=False):
        self.content = fh.getvalue().decode("utf-8")


class _FakeDownloader:
    def __init__(self, fh, request):
        self._fh = fh
        self._request = request

    def next_chunk(self):
        self._fh.write(self._request.execute())
        return None, True


def _client(fake):
    return dc.DriveClient(service=fake)


class ScopeTests(unittest.TestCase):
    """The immediate cause of the 2026-09-19 failure."""

    def test_stale_full_drive_scope_is_not_requested_on_refresh(self):
        token = {"refresh_token": "r", "scopes": [
            "https://www.googleapis.com/auth/drive"]}
        self.assertIsNone(dc._requested_scopes(token))

    def test_drive_file_scope_is_requested_as_is(self):
        token = {"refresh_token": "r", "scopes": [dc.DRIVE_FILE_SCOPE]}
        self.assertEqual(dc._requested_scopes(token), [dc.DRIVE_FILE_SCOPE])

    def test_broad_scope_dropped_but_others_kept(self):
        token = {"scopes": [
            "https://www.googleapis.com/auth/drive",
            dc.DRIVE_FILE_SCOPE,
        ]}
        self.assertEqual(dc._requested_scopes(token), [dc.DRIVE_FILE_SCOPE])

    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"GOOGLE_DRIVE_SCOPES": "a b"}):
            self.assertEqual(dc._requested_scopes({"scopes": ["x"]}), ["a", "b"])

    def test_token_without_cached_access_token_is_accepted(self):
        token = json.dumps({"refresh_token": "r", "client_id": "c",
                            "client_secret": "s", "scopes": [dc.DRIVE_FILE_SCOPE]})
        with mock.patch.dict(os.environ, {"GOOGLE_DRIVE_TOKEN_JSON": token},
                             clear=False):
            creds = dc._build_credentials()
        self.assertIsNone(creds.token)
        self.assertEqual(creds.refresh_token, "r")


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(dc, "MediaIoBaseUpload", _FakeMedia),
            mock.patch.object(dc, "MediaIoBaseDownload", _FakeDownloader),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for var in ("GOOGLE_DRIVE_ROOT_FOLDER_ID", "GOOGLE_DRIVE_SYNC_STATE_FILE_ID",
                    "GOOGLE_DRIVE_SCOPES"):
            os.environ.pop(var, None)

    def test_invisible_root_folder_raises_instead_of_returning_none(self):
        fake = FakeDrive()
        fake.add_folder(dc.ROOT_FOLDER_NAME, visible=False)
        with self.assertRaises(dc.DriveAccessError) as ctx:
            _client(fake).root_folder_id()
        self.assertIn("drive.file", str(ctx.exception))

    def test_root_folder_id_env_var_is_used_without_a_name_query(self):
        fake = FakeDrive()
        root = fake.add_folder("some-other-name")
        os.environ["GOOGLE_DRIVE_ROOT_FOLDER_ID"] = root
        self.assertEqual(_client(fake).root_folder_id(), root)

    def test_root_folder_created_only_when_asked(self):
        fake = FakeDrive()
        client = _client(fake)
        created = client.root_folder_id(create=True)
        self.assertEqual(fake.nodes[created]["name"], dc.ROOT_FOLDER_NAME)
        self.assertTrue(any("created a new" in w for w in client.warnings))

    def test_duplicate_names_resolve_to_newest_and_warn(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        fake.add(dc.SYNC_STATE_NAME, "application/json", root,
                 '{"last_synced_date": "2026-09-14"}', modified="2026-09-14T00:00:00Z")
        newest = fake.add(dc.SYNC_STATE_NAME, "application/json", root,
                          '{"last_synced_date": "2026-09-16"}',
                          modified="2026-09-16T00:00:00Z")
        client = _client(fake)
        self.assertEqual(client.read_sync_state()["last_synced_date"], "2026-09-16")
        self.assertEqual(client.sync_state_file_id(), newest)
        self.assertTrue(any("2 files named" in w for w in client.warnings))

    def test_sync_state_write_updates_in_place_and_creates_no_duplicate(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        existing = fake.add(dc.SYNC_STATE_NAME, "application/json", root,
                            '{"last_synced_date": "2026-09-16"}')
        client = _client(fake)
        client.write_sync_state({"last_synced_date": "2026-09-20"})
        self.assertEqual(fake.created_ids, [])
        self.assertEqual(fake.updated_ids, [existing])
        state = json.loads(fake.nodes[existing]["content"])
        self.assertEqual(state["last_synced_date"], "2026-09-20")
        self.assertEqual(state["file_ids"]["root"], root)

    def test_sync_state_write_preserves_the_cached_id_map(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        fake.add(dc.SYNC_STATE_NAME, "application/json", root,
                 json.dumps({"last_synced_date": "2026-09-16",
                             "file_ids": {"csv/2026-01": "kept"}}))
        client = _client(fake)
        client.read_sync_state()
        merged = client.write_sync_state({"last_synced_date": "2026-09-20"})
        self.assertEqual(merged["file_ids"]["csv/2026-01"], "kept")

    def test_recorded_but_unreachable_month_raises_not_missing(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        month_folder = fake.add_folder("2026-09", root)
        hidden = fake.add("transactions_2026-09.csv", "text/csv", month_folder,
                          "Date\n", visible=False)
        fake.add(dc.SYNC_STATE_NAME, "application/json", root, json.dumps(
            {"last_synced_date": "2026-09-16", "file_ids": {"csv/2026-09": hidden}}))
        client = _client(fake)
        client.read_sync_state()
        with self.assertRaises(dc.DriveAccessError):
            client.read_month_csv("2026-09")

    def test_month_with_no_file_at_all_is_reported_absent(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        fake.add_folder("2026-09", root)
        self.assertIsNone(_client(fake).read_month_csv("2026-09"))

    def test_month_csv_round_trip_records_its_id(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        client = _client(fake)
        client.write_month_csv("2026-09", "Date\n2026-09-01\n")
        self.assertEqual(client.read_month_csv("2026-09"), "Date\n2026-09-01\n")
        # A second write updates rather than creating a second CSV.
        created_before = len(fake.created_ids)
        client.write_month_csv("2026-09", "Date\n2026-09-02\n")
        self.assertEqual(len(fake.created_ids), created_before)
        self.assertEqual(client.read_month_csv("2026-09"), "Date\n2026-09-02\n")

    def test_list_month_folders_ignores_non_month_names(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        fake.add_folder("2026-09", root)
        fake.add_folder("2025-12", root)
        fake.add_folder("archive-notes", root)
        self.assertEqual(_client(fake).list_month_folders(), ["2025-12", "2026-09"])

    def test_names_with_quotes_do_not_break_the_query(self):
        fake = FakeDrive()
        root = fake.add_folder(dc.ROOT_FOLDER_NAME)
        fid = fake.add("o'brien's.csv", "text/csv", root, "x")
        self.assertEqual(_client(fake).find_child(root, "o'brien's.csv"), fid)


class AnalysisGuardTests(unittest.TestCase):
    def test_whole_window_unreadable_refuses_to_report_zeros(self):
        from . import analysis

        class Blind:
            def read_month_csv(self, month):
                return None

        with self.assertRaises(dc.DriveAccessError) as ctx:
            analysis.load_window_rows(drive=Blind())
        self.assertIn("doctor", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
