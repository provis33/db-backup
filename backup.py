"""Бэкап PostgreSQL/MySQL → ZIP → Google Drive / Dropbox.

RU: cron-friendly: дамп, архив, облако, чистка старше RETENTION_DAYS.
EN: cron-friendly dump → zip → cloud → prune old local zips.
"""

from __future__ import annotations

import logging
import os
import sys
import zipfile
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import load_dotenv

from services.dumper import DbConfig, DumpError, create_dump
from services.uploader import UploadError, upload_zip

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "backup.log"

logger = logging.getLogger("backup")


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_h = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_h.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_h)
    root.addHandler(console)


def load_settings() -> dict:
    load_dotenv(ROOT / ".env")

    def req(key: str) -> str:
        val = os.getenv(key, "").strip()
        if not val or val.startswith("your_"):
            raise RuntimeError(f"заполни {key} в .env")
        return val

    backup_dir = Path(os.getenv("BACKUP_DIR", "backups"))
    if not backup_dir.is_absolute():
        backup_dir = ROOT / backup_dir

    return {
        "db": DbConfig(
            db_type=req("DB_TYPE"),
            host=req("DB_HOST"),
            port=int(os.getenv("DB_PORT", "5432")),
            user=req("DB_USER"),
            password=req("DB_PASSWORD"),
            name=req("DB_NAME"),
        ),
        "backup_dir": backup_dir,
        "retention_days": int(os.getenv("RETENTION_DAYS", "7")),
        "cloud_provider": os.getenv("CLOUD_PROVIDER", "auto").strip(),
        "gdrive_credentials": os.getenv("GDRIVE_CREDENTIALS_JSON", "").strip(),
        "gdrive_folder_id": os.getenv("GDRIVE_FOLDER_ID", "").strip(),
        "dropbox_token": os.getenv("DROPBOX_TOKEN", "").strip(),
        "dropbox_path": os.getenv("DROPBOX_PATH", "/db-backups").strip() or "/db-backups",
        "tg_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "tg_chat": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    }


def zip_sql(sql_path: Path) -> Path:
    zip_path = sql_path.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(sql_path, arcname=sql_path.name)
    logger.info("zip: %s (%.1f KB)", zip_path.name, zip_path.stat().st_size / 1024)
    # sql после упаковки не обязателен локально
    try:
        sql_path.unlink()
    except OSError as exc:
        logger.warning("не удалили sql: %s", exc)
    return zip_path


def prune_old(backup_dir: Path, retention_days: int) -> int:
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for path in backup_dir.glob("backup_*.zip"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
            logger.info("prune: %s", path.name)
    # старые .sql на всякий
    for path in backup_dir.glob("backup_*.sql"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def notify_telegram(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id or "your_telegram" in token:
        logger.info("telegram skip (не настроен)")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
        if not resp.ok:
            logger.error("telegram API %s: %s", resp.status_code, resp.text[:200])
    except requests.RequestException as exc:
        logger.error("telegram fail: %s", exc)


def main() -> int:
    setup_logging()
    logger.info("=== backup start ===")

    try:
        cfg = load_settings()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 2

    tg_token, tg_chat = cfg["tg_token"], cfg["tg_chat"]
    backup_dir: Path = cfg["backup_dir"]
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1) dump
    try:
        sql_path = create_dump(cfg["db"], backup_dir)
    except DumpError as exc:
        logger.error("dump failed: %s", exc)
        notify_telegram(
            tg_token,
            tg_chat,
            f"❌ DB backup dump failed ({cfg['db'].db_type}/{cfg['db'].name}):\n{exc}",
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("dump crashed: %s", exc)
        notify_telegram(
            tg_token,
            tg_chat,
            f"❌ DB backup dump crashed ({cfg['db'].name}):\n{exc}",
        )
        return 1

    # 2) zip
    try:
        zip_path = zip_sql(sql_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("zip failed: %s", exc)
        notify_telegram(tg_token, tg_chat, f"❌ DB backup zip failed:\n{exc}")
        return 1

    # 3) upload — при ошибке оставляем локально
    remote = None
    try:
        remote = upload_zip(
            zip_path,
            provider=cfg["cloud_provider"],
            gdrive_credentials=cfg["gdrive_credentials"],
            gdrive_folder_id=cfg["gdrive_folder_id"],
            dropbox_token=cfg["dropbox_token"],
            dropbox_path=cfg["dropbox_path"],
        )
        logger.info("uploaded: %s", remote)
    except UploadError as exc:
        logger.error("upload failed, ZIP остаётся локально (%s): %s", zip_path, exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("upload crashed, ZIP локально (%s): %s", zip_path, exc)

    # 4) prune
    removed = prune_old(backup_dir, cfg["retention_days"])
    logger.info(
        "=== backup done | zip=%s | remote=%s | pruned=%s ===",
        zip_path.name,
        remote or "LOCAL_ONLY",
        removed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
