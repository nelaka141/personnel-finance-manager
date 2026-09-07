"""Google Drive I/O for the personal finance bot.

Uses google-api-python-client directly (not an MCP connector), authenticated
from the OAuth token JSON stored in the GOOGLE_DRIVE_TOKEN_JSON env var.
"""
import io
import json
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

ROOT_FOLDER_NAME = "personnel-finances"
FOLDER_MIME = "application/vnd.google-apps.folder"


def _build_credentials():
    raw = os.environ.get("GOOGLE_DRIVE_TOKEN_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_DRIVE_TOKEN_JSON env var is not set")
    v = json.loads(raw)
    return Credentials(
        token=v["token"],
        refresh_token=v.get("refresh_token"),
        token_uri=v.get("token_uri"),
        client_id=v.get("client_id"),
        client_secret=v.get("client_secret"),
        scopes=v.get("scopes"),
    )


class DriveClient:
    def __init__(self):
        self.service = build("drive", "v3", credentials=_build_credentials())
        self._root_id = None

    def root_folder_id(self):
        if self._root_id:
            return self._root_id
        res = self.service.files().list(
            q=(f"name = '{ROOT_FOLDER_NAME}' and mimeType = '{FOLDER_MIME}' "
               "and trashed = false"),
            fields="files(id,name)",
        ).execute()
        files = res.get("files", [])
        if not files:
            raise RuntimeError(f"Root folder '{ROOT_FOLDER_NAME}' not found in Drive")
        self._root_id = files[0]["id"]
        return self._root_id

    def find_child(self, parent_id, name, mime_type=None):
        safe_name = name.replace("'", "\\'")
        q = f"'{parent_id}' in parents and name = '{safe_name}' and trashed = false"
        if mime_type:
            q += f" and mimeType = '{mime_type}'"
        res = self.service.files().list(q=q, fields="files(id,name)").execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def get_or_create_month_folder(self, month):
        root = self.root_folder_id()
        fid = self.find_child(root, month, FOLDER_MIME)
        if fid:
            return fid
        body = {"name": month, "mimeType": FOLDER_MIME, "parents": [root]}
        return self.service.files().create(body=body, fields="id").execute()["id"]

    def download_text(self, file_id):
        request = self.service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8")

    def upload_text(self, parent_id, name, text, mime_type):
        existing_id = self.find_child(parent_id, name)
        media = MediaIoBaseUpload(
            io.BytesIO(text.encode("utf-8")), mimetype=mime_type, resumable=False
        )
        if existing_id:
            return self.service.files().update(fileId=existing_id, media_body=media).execute()
        body = {"name": name, "parents": [parent_id]}
        return self.service.files().create(body=body, media_body=media, fields="id").execute()

    def read_month_csv(self, month):
        root = self.root_folder_id()
        folder_id = self.find_child(root, month, FOLDER_MIME)
        if not folder_id:
            return None
        file_id = self.find_child(folder_id, f"transactions_{month}.csv")
        if not file_id:
            return None
        return self.download_text(file_id)

    def write_month_csv(self, month, text):
        folder_id = self.get_or_create_month_folder(month)
        self.upload_text(folder_id, f"transactions_{month}.csv", text, "text/csv")

    def read_sync_state(self):
        root = self.root_folder_id()
        fid = self.find_child(root, "sync_state.json")
        if not fid:
            return None
        return json.loads(self.download_text(fid))

    def write_sync_state(self, state):
        root = self.root_folder_id()
        self.upload_text(root, "sync_state.json", json.dumps(state, indent=2), "application/json")
