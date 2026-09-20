"""Google Drive I/O for the personal finance bot.

Uses google-api-python-client directly (not an MCP connector), authenticated
from the OAuth token JSON stored in the GOOGLE_DRIVE_TOKEN_JSON env var.

This module is written for the **drive.file** scope, not full drive access.
Two things follow from that scope and shape everything below:

1. The refresh request must not ask for a scope the user never granted.
   google-auth sends whatever `Credentials(scopes=...)` holds as the `scope`
   parameter of the refresh call, and Google rejects the entire refresh with
   `invalid_scope` when it asks for more than the grant -- which is how the
   2026-09-19 run died, since the stored token JSON still lists the old
   full-drive scope. See _requested_scopes().

2. Under drive.file the app only sees files it created itself (or that the
   user explicitly opened with it), so a global "find the folder by name"
   query is no longer a reliable way to locate the archive: it silently
   returns nothing for files belonging to another app. So ids come first
   (GOOGLE_DRIVE_ROOT_FOLDER_ID, then the id map cached in sync_state.json)
   and a name query is only the fallback. When a file that should exist is
   unreachable we raise DriveAccessError instead of reporting "absent",
   because "invisible to this app" and "not there" mean very different
   things and only one of them is fixable by re-granting access.
"""
import io
import json
import os
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

ROOT_FOLDER_NAME = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_NAME", "personnel-finances")
FOLDER_MIME = "application/vnd.google-apps.folder"
SYNC_STATE_NAME = "sync_state.json"

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
# Scopes the OAuth client no longer holds. Re-requesting any of these on a
# refresh is what produces `invalid_scope`.
BROAD_DRIVE_SCOPES = frozenset({
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
})

# Harmless on My Drive, required if the archive ever moves to a shared drive.
_ITEM_ARGS = {"supportsAllDrives": True}
_LIST_ARGS = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}

_REGRANT_HINT = (
    "Under the drive.file scope this app can only reach files it created "
    "itself or that you explicitly opened with it. Re-run the Drive OAuth "
    "flow for this client and open the 'personnel-finances' folder with it, "
    "or set GOOGLE_DRIVE_ROOT_FOLDER_ID to a folder this app owns."
)


class DriveConfigError(RuntimeError):
    """The Drive credential itself is missing or unusable."""


class DriveAccessError(RuntimeError):
    """Drive refused a file this app is not allowed to see or touch.

    Distinct from "the file does not exist": this one is fixable by
    re-granting access, and must never be silently read as an empty month.
    """


def _escape(value):
    """Escape a value for interpolation into a Drive `q` string."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _is_access_error(err):
    return isinstance(err, HttpError) and getattr(err, "resp", None) is not None \
        and err.resp.status in (401, 403, 404)


def _load_token():
    raw = os.environ.get("GOOGLE_DRIVE_TOKEN_JSON")
    if not raw:
        raise DriveConfigError("GOOGLE_DRIVE_TOKEN_JSON env var is not set")
    try:
        return json.loads(raw)
    except ValueError as err:
        raise DriveConfigError(
            f"GOOGLE_DRIVE_TOKEN_JSON is not valid JSON: {err}"
        ) from err


def _requested_scopes(token_json):
    """The scope list to send on refresh, or None to send no `scope` at all.

    A stale broad scope in the token JSON is dropped rather than
    re-requested: asking for `.../auth/drive` when only `drive.file` was
    granted fails the whole refresh with `invalid_scope`. Omitting `scope`
    entirely leaves the refresh token with exactly the scopes it was granted,
    which is what we want. GOOGLE_DRIVE_SCOPES overrides this if a future
    grant needs something specific.
    """
    override = os.environ.get("GOOGLE_DRIVE_SCOPES")
    if override:
        return override.split()
    declared = token_json.get("scopes") or []
    narrowed = [s for s in declared if s not in BROAD_DRIVE_SCOPES]
    return narrowed or None


def _build_credentials():
    v = _load_token()
    return Credentials(
        # .get(), not ["token"]: a token JSON that only carries a refresh
        # token is valid -- the access token is fetched on first use.
        token=v.get("token"),
        refresh_token=v.get("refresh_token"),
        token_uri=v.get("token_uri"),
        client_id=v.get("client_id"),
        client_secret=v.get("client_secret"),
        scopes=_requested_scopes(v),
    )


class DriveClient:
    def __init__(self, service=None):
        self.service = service or build(
            "drive", "v3", credentials=_build_credentials(), cache_discovery=False
        )
        self._root_id = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID") or None
        self._root_verified = False
        # "path" -> file id, e.g. {"root": ..., "months/2026-09": ...}.
        # Seeded from sync_state.json and written back to it, so a run never
        # has to rediscover anything by name.
        self._ids = {}
        self._sync_state_id = os.environ.get("GOOGLE_DRIVE_SYNC_STATE_FILE_ID") or None
        self.warnings = []

    def _warn(self, message):
        self.warnings.append(message)
        print(f"drive: warning: {message}", file=sys.stderr)

    # -- low-level helpers --------------------------------------------------

    def _get_metadata(self, file_id, fields="id,name,mimeType,modifiedTime"):
        try:
            return self.service.files().get(
                fileId=file_id, fields=fields, **_ITEM_ARGS
            ).execute()
        except HttpError as err:
            if _is_access_error(err):
                raise DriveAccessError(
                    f"Drive file id {file_id} is not reachable by this app "
                    f"({err.resp.status}). {_REGRANT_HINT}"
                ) from err
            raise

    def _list_children(self, parent_id, name=None, mime_type=None):
        q = [f"'{_escape(parent_id)}' in parents", "trashed = false"]
        if name is not None:
            q.append(f"name = '{_escape(name)}'")
        if mime_type:
            q.append(f"mimeType = '{_escape(mime_type)}'")
        try:
            res = self.service.files().list(
                q=" and ".join(q),
                fields="files(id,name,mimeType,modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=1000,
                **_LIST_ARGS,
            ).execute()
        except HttpError as err:
            if _is_access_error(err):
                raise DriveAccessError(
                    f"Cannot list the contents of folder {parent_id} "
                    f"({err.resp.status}). {_REGRANT_HINT}"
                ) from err
            raise
        return res.get("files", [])

    def find_child(self, parent_id, name, mime_type=None):
        """Newest visible child of `parent_id` called `name`, or None.

        Drive allows several files to share a name in one folder, and the
        archive root has in fact accumulated duplicates (one sync_state.json
        per run, from runs that could not see the previous one and created a
        new file instead of updating it). Picking the most recently modified
        makes the choice deterministic rather than "whatever the API listed
        first", and the duplicates are reported so they can be cleaned up.
        """
        matches = self._list_children(parent_id, name=name, mime_type=mime_type)
        if not matches:
            return None
        if len(matches) > 1:
            self._warn(
                f"{len(matches)} files named '{name}' in folder {parent_id}; "
                f"using the most recently modified "
                f"({matches[0]['id']}, {matches[0].get('modifiedTime')}). "
                "Delete the stale copies in Drive."
            )
        return matches[0]["id"]

    # -- root folder --------------------------------------------------------

    def root_folder_id(self, create=False):
        if self._root_id and self._root_verified:
            return self._root_id

        if self._root_id:
            meta = self._get_metadata(self._root_id)
            if meta.get("mimeType") != FOLDER_MIME:
                raise DriveAccessError(
                    f"GOOGLE_DRIVE_ROOT_FOLDER_ID={self._root_id} is not a "
                    f"folder (mimeType {meta.get('mimeType')})"
                )
            self._root_verified = True
            self._ids["root"] = self._root_id
            return self._root_id

        matches = []
        try:
            res = self.service.files().list(
                q=(f"name = '{_escape(ROOT_FOLDER_NAME)}' "
                   f"and mimeType = '{FOLDER_MIME}' and trashed = false"),
                fields="files(id,name,modifiedTime)",
                orderBy="modifiedTime desc",
                **_LIST_ARGS,
            ).execute()
            matches = res.get("files", [])
        except HttpError as err:
            if not _is_access_error(err):
                raise

        if len(matches) > 1:
            self._warn(
                f"{len(matches)} folders named '{ROOT_FOLDER_NAME}' are visible; "
                f"using {matches[0]['id']}. Set GOOGLE_DRIVE_ROOT_FOLDER_ID to "
                "pin the right one."
            )
        if matches:
            self._root_id = matches[0]["id"]
            self._root_verified = True
            self._ids["root"] = self._root_id
            return self._root_id

        if not create:
            raise DriveAccessError(
                f"Root folder '{ROOT_FOLDER_NAME}' is not visible to this app. "
                f"{_REGRANT_HINT}"
            )

        body = {"name": ROOT_FOLDER_NAME, "mimeType": FOLDER_MIME}
        created = self.service.files().create(
            body=body, fields="id", **_ITEM_ARGS
        ).execute()
        self._root_id = created["id"]
        self._root_verified = True
        self._ids["root"] = self._root_id
        self._warn(
            f"created a new '{ROOT_FOLDER_NAME}' folder ({self._root_id}); the "
            "previous archive, if any, is not visible under drive.file. Set "
            "GOOGLE_DRIVE_ROOT_FOLDER_ID to this id to pin it."
        )
        return self._root_id

    def get_or_create_month_folder(self, month):
        cached = self._ids.get(f"months/{month}")
        if cached:
            return cached
        root = self.root_folder_id(create=True)
        fid = self.find_child(root, month, FOLDER_MIME)
        if not fid:
            body = {"name": month, "mimeType": FOLDER_MIME, "parents": [root]}
            fid = self.service.files().create(
                body=body, fields="id", **_ITEM_ARGS
            ).execute()["id"]
        self._ids[f"months/{month}"] = fid
        return fid

    # -- content ------------------------------------------------------------

    def download_text(self, file_id):
        try:
            request = self.service.files().get_media(fileId=file_id, **_ITEM_ARGS)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        except HttpError as err:
            if _is_access_error(err):
                raise DriveAccessError(
                    f"Cannot download Drive file id {file_id} "
                    f"({err.resp.status}). {_REGRANT_HINT}"
                ) from err
            raise
        return buf.getvalue().decode("utf-8")

    def upload_text(self, parent_id, name, text, mime_type, file_id=None):
        """Write `text` to `name` under `parent_id`; returns the file id.

        `file_id` updates that exact file. Without it we look the name up and
        update what we find, creating only when nothing is visible -- the
        create path is what produced the duplicate sync_state.json files, so
        callers that know the id should always pass it.
        """
        target_id = file_id or self.find_child(parent_id, name)

        def _media():
            return MediaIoBaseUpload(
                io.BytesIO(text.encode("utf-8")), mimetype=mime_type, resumable=False
            )

        if target_id:
            try:
                return self.service.files().update(
                    fileId=target_id, media_body=_media(), fields="id", **_ITEM_ARGS
                ).execute()["id"]
            except HttpError as err:
                if not _is_access_error(err):
                    raise
                if err.resp.status != 404:
                    # 403: the file is there but off limits to this app.
                    # Creating a replacement would just add a duplicate.
                    raise DriveAccessError(
                        f"Cannot write '{name}' in folder {parent_id} "
                        f"({err.resp.status}). {_REGRANT_HINT}"
                    ) from err
                # 404: the recorded id is gone (deleted in Drive). Fall
                # through and write a new file rather than wedging the sync
                # on a stale id forever.
                self._warn(
                    f"recorded id {target_id} for '{name}' no longer exists; "
                    "writing a new file"
                )

        try:
            body = {"name": name, "parents": [parent_id]}
            return self.service.files().create(
                body=body, media_body=_media(), fields="id", **_ITEM_ARGS
            ).execute()["id"]
        except HttpError as err:
            if _is_access_error(err):
                raise DriveAccessError(
                    f"Cannot write '{name}' in folder {parent_id} "
                    f"({err.resp.status}). {_REGRANT_HINT}"
                ) from err
            raise

    # -- monthly CSVs -------------------------------------------------------

    def _month_csv_name(self, month):
        return f"transactions_{month}.csv"

    def month_csv_id(self, month):
        """Id of this month's CSV, or None if nothing is visible for it.

        Raises DriveAccessError -- rather than returning None -- when the id
        map says the file exists but Drive will not hand it over, so an
        unreadable month is never mistaken for a month with no spending.
        """
        key = f"csv/{month}"
        cached = self._ids.get(key)
        if cached:
            self._get_metadata(cached, fields="id")
            return cached

        try:
            root = self.root_folder_id()
        except DriveAccessError:
            return None
        folder_id = self._ids.get(f"months/{month}") or \
            self.find_child(root, month, FOLDER_MIME)
        if not folder_id:
            return None
        self._ids[f"months/{month}"] = folder_id
        file_id = self.find_child(folder_id, self._month_csv_name(month))
        if file_id:
            self._ids[key] = file_id
        return file_id

    def read_month_csv(self, month):
        file_id = self.month_csv_id(month)
        if not file_id:
            return None
        return self.download_text(file_id)

    def write_month_csv(self, month, text):
        folder_id = self.get_or_create_month_folder(month)
        file_id = self.upload_text(
            folder_id, self._month_csv_name(month), text, "text/csv",
            file_id=self._ids.get(f"csv/{month}"),
        )
        self._ids[f"csv/{month}"] = file_id
        return file_id

    def list_month_folders(self):
        """Month folder names (YYYY-MM) visible in the archive root."""
        root = self.root_folder_id()
        names = []
        for f in self._list_children(root, mime_type=FOLDER_MIME):
            name = f.get("name", "")
            if len(name) == 7 and name[4] == "-" and name.replace("-", "").isdigit():
                names.append(name)
                self._ids.setdefault(f"months/{name}", f["id"])
        return sorted(set(names))

    # -- sync state ---------------------------------------------------------

    def sync_state_file_id(self):
        if self._sync_state_id:
            return self._sync_state_id
        try:
            root = self.root_folder_id()
        except DriveAccessError:
            return None
        self._sync_state_id = self.find_child(root, SYNC_STATE_NAME)
        return self._sync_state_id

    def read_sync_state(self):
        file_id = self.sync_state_file_id()
        if not file_id:
            return None
        state = json.loads(self.download_text(file_id))
        # Seed the id map so the rest of the run addresses files by id.
        for key, value in (state.get("file_ids") or {}).items():
            self._ids.setdefault(key, value)
        return state

    def write_sync_state(self, state):
        """Merge `state` into sync_state.json, keeping the cached id map.

        Always updates the same file id within a run, so a run can no longer
        leave a second sync_state.json behind.
        """
        root = self.root_folder_id(create=True)
        file_id = self.sync_state_file_id()
        current = {}
        if file_id:
            try:
                current = json.loads(self.download_text(file_id))
            except (DriveAccessError, ValueError):
                current = {}
        merged = dict(current)
        merged.update(state)
        file_ids = dict(merged.get("file_ids") or {})
        file_ids.update(self._ids)
        file_ids["root"] = root
        merged["file_ids"] = file_ids
        new_id = self.upload_text(
            root, SYNC_STATE_NAME, json.dumps(merged, indent=2),
            "application/json", file_id=file_id,
        )
        self._sync_state_id = new_id
        return merged
