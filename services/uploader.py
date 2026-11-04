"""Загрузка ZIP в Google Drive (service account) или Dropbox.

RU: Выбор провайдера по CLOUD_PROVIDER / наличию ключей.
EN: Provider from CLOUD_PROVIDER or whichever credentials exist.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("backup")


class UploadError(RuntimeError):
    pass


def upload_zip(
    zip_path: Path,
    *,
    provider: str,
    gdrive_credentials: str,
    gdrive_folder_id: str,
    dropbox_token: str,
    dropbox_path: str = "/db-backups",
) -> str:
    """Вернуть человекочитаемый id/путь в облаке."""

    choice = _resolve_provider(
        provider,
        gdrive_credentials=gdrive_credentials,
        gdrive_folder_id=gdrive_folder_id,
        dropbox_token=dropbox_token,
    )
    if choice == "gdrive":
        return upload_gdrive(zip_path, gdrive_credentials, gdrive_folder_id)
    if choice == "dropbox":
        return upload_dropbox(zip_path, dropbox_token, dropbox_path)
    raise UploadError(f"неизвестный провайдер: {choice}")


def _resolve_provider(
    provider: str,
    *,
    gdrive_credentials: str,
    gdrive_folder_id: str,
    dropbox_token: str,
) -> str:
    p = (provider or "auto").strip().lower()
    gdrive_ok = bool(gdrive_credentials and gdrive_folder_id) and "your_" not in gdrive_folder_id
    dropbox_ok = bool(dropbox_token) and "your_dropbox" not in dropbox_token

    if p in ("gdrive", "google", "drive"):
        if not gdrive_ok:
            raise UploadError("CLOUD_PROVIDER=gdrive, но GDRIVE_* не заданы")
        return "gdrive"
    if p == "dropbox":
        if not dropbox_ok:
            raise UploadError("CLOUD_PROVIDER=dropbox, но DROPBOX_TOKEN не задан")
        return "dropbox"
    if p in ("auto", ""):
        if gdrive_ok:
            return "gdrive"
        if dropbox_ok:
            return "dropbox"
        raise UploadError(
            "нет облачных ключей: задай GDRIVE_CREDENTIALS_JSON+GDRIVE_FOLDER_ID "
            "или DROPBOX_TOKEN"
        )
    raise UploadError(f"CLOUD_PROVIDER={provider!r} — ждём gdrive|dropbox|auto")


def upload_gdrive(zip_path: Path, credentials_json: str, folder_id: str) -> str:
    creds_path = Path(credentials_json)
    if not creds_path.is_file():
        raise UploadError(f"GDRIVE_CREDENTIALS_JSON не найден: {creds_path}")

    try:
        from oauth2client.service_account import ServiceAccountCredentials
        from pydrive2.auth import GoogleAuth
        from pydrive2.drive import GoogleDrive
    except ImportError as exc:
        raise UploadError("установи pydrive2 (pip install -r requirements.txt)") from exc

    scopes = ["https://www.googleapis.com/auth/drive"]
    gauth = GoogleAuth()
    gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name(
        str(creds_path), scopes
    )
    drive = GoogleDrive(gauth)

    meta = {
        "title": zip_path.name,
        "parents": [{"id": folder_id}],
    }
    f = drive.CreateFile(meta)
    f.SetContentFile(str(zip_path))
    f.Upload()
    file_id = f.get("id") or ""
    logger.info("gdrive upload ok: %s (id=%s)", zip_path.name, file_id)
    return f"gdrive:{file_id}"


def upload_dropbox(zip_path: Path, token: str, remote_dir: str = "/db-backups") -> str:
    try:
        import dropbox
        from dropbox.files import WriteMode
    except ImportError as exc:
        raise UploadError("установи dropbox (pip install -r requirements.txt)") from exc

    remote_dir = remote_dir.rstrip("/") or "/db-backups"
    dest = f"{remote_dir}/{zip_path.name}"

    dbx = dropbox.Dropbox(token)
    # chunked на случай больших дампов
    chunk = 8 * 1024 * 1024
    size = zip_path.stat().st_size

    with zip_path.open("rb") as fh:
        if size <= chunk:
            dbx.files_upload(fh.read(), dest, mode=WriteMode("overwrite"))
        else:
            start = dbx.files_upload_session_start(fh.read(chunk))
            offset = chunk
            while offset < size:
                if size - offset <= chunk:
                    dbx.files_upload_session_finish(
                        fh.read(chunk),
                        dropbox.files.UploadSessionCursor(start.session_id, offset),
                        dropbox.files.CommitInfo(path=dest, mode=WriteMode("overwrite")),
                    )
                    break
                dbx.files_upload_session_append_v2(
                    fh.read(chunk),
                    dropbox.files.UploadSessionCursor(start.session_id, offset),
                )
                offset += chunk

    logger.info("dropbox upload ok: %s", dest)
    return f"dropbox:{dest}"
