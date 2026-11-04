# db-backup

Дамп **PostgreSQL** / **MySQL** → ZIP → **Google Drive** или **Dropbox**, чистка локальных архивов старше N дней. Для cron.

Пароли и токены — **только** в `.env`, в код не попадают.

## Установка

```bash
cd db-backup
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Нужны клиенты в PATH:

- Postgres: `pg_dump`
- MySQL/MariaDB: `mysqldump`

## .env

```env
DB_TYPE=postgres          # или mysql
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=...
DB_PASSWORD=...
DB_NAME=...

CLOUD_PROVIDER=auto       # gdrive | dropbox | auto
GDRIVE_CREDENTIALS_JSON=C:\secrets\sa.json
GDRIVE_FOLDER_ID=...
DROPBOX_TOKEN=...
DROPBOX_PATH=/db-backups

TELEGRAM_BOT_TOKEN=...    # опционально, алерт при ошибке дампа
TELEGRAM_CHAT_ID=...

BACKUP_DIR=backups
RETENTION_DAYS=7
```

## Google Drive (service account)

1. Google Cloud Console → создать проект → включить **Google Drive API**.
2. IAM → Service Account → ключ JSON → скачать.
3. Создай папку на Drive, расшарь её на email сервисного аккаунта (`...@....iam.gserviceaccount.com`) с правом редактора.
4. `GDRIVE_FOLDER_ID` — id из URL папки: `https://drive.google.com/drive/folders/<ID>`.
5. `GDRIVE_CREDENTIALS_JSON` — путь к JSON-ключу.

## Dropbox

1. [Dropbox App Console](https://www.dropbox.com/developers/apps) → Create app → Scoped access.
2. Permissions: `files.content.write`, `files.content.read`.
3. Generate access token → `DROPBOX_TOKEN`.
4. Файлы уйдут в `DROPBOX_PATH` (по умолчанию `/db-backups`).

## Запуск

```bash
python backup.py
```

Cron (ежедневно в 3:00):

```cron
0 3 * * * cd /path/to/db-backup && .venv/bin/python backup.py >> backup.log 2>&1
```

Windows Task Scheduler: `python backup.py` из папки проекта.

## Поведение

| этап | при ошибке |
|------|------------|
| дамп | Telegram (если настроен), exit 1 |
| ZIP | Telegram, exit 1 |
| облако | ZIP остаётся в `backups/`, пишем в `backup.log`, скрипт не падает |
| prune | локальные `backup_*.zip` старше `RETENTION_DAYS` |

Имена файлов: `backup_YYYY-MM-DD_HH-MM.sql` → `.zip`.

## Структура

```
backup.py              # оркестратор
services/dumper.py     # pg_dump / mysqldump
services/uploader.py   # Drive / Dropbox
```
