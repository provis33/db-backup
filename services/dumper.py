"""Дамп PostgreSQL / MySQL через pg_dump / mysqldump.

RU: Пароль только через env процесса (PGPASSWORD / MYSQL_PWD), не в argv.
EN: Password via process env only — never on the command line.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("backup")


@dataclass
class DbConfig:
    db_type: str  # postgres | mysql
    host: str
    port: int
    user: str
    password: str
    name: str


class DumpError(RuntimeError):
    pass


def dump_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M")
    return f"backup_{stamp}.sql"


def run_dump(cfg: DbConfig, out_sql: Path) -> Path:
    out_sql.parent.mkdir(parents=True, exist_ok=True)
    db_type = cfg.db_type.strip().lower()

    if db_type in ("postgres", "postgresql", "pg"):
        cmd, env = _pg_cmd(cfg, out_sql)
    elif db_type in ("mysql", "mariadb"):
        cmd, env = _mysql_cmd(cfg, out_sql)
    else:
        raise DumpError(f"неизвестный DB_TYPE={cfg.db_type!r} (postgres|mysql)")

    logger.info("dump: %s → %s", db_type, out_sql.name)
    # не логируем полный cmd с хостом/юзером лишний раз в debug можно
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        tool = "pg_dump" if db_type.startswith("post") or db_type == "pg" else "mysqldump"
        raise DumpError(
            f"{tool} не найден в PATH. Установи клиент БД и проверь PATH."
        ) from exc

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        # пароль из stderr вычищаем на всякий
        err = err.replace(cfg.password, "***") if cfg.password else err
        raise DumpError(f"дамп упал (code={result.returncode}): {err[:500]}")

    if not out_sql.exists() or out_sql.stat().st_size == 0:
        raise DumpError("файл дампа пустой или не создан")

    logger.info("dump ok: %.1f KB", out_sql.stat().st_size / 1024)
    return out_sql


def _pg_cmd(cfg: DbConfig, out_sql: Path) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env["PGPASSWORD"] = cfg.password
    cmd = [
        "pg_dump",
        "-h", cfg.host,
        "-p", str(cfg.port),
        "-U", cfg.user,
        "-d", cfg.name,
        "-F", "p",  # plain SQL
        "-f", str(out_sql),
        "--no-password",
    ]
    return cmd, env


def _mysql_cmd(cfg: DbConfig, out_sql: Path) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env["MYSQL_PWD"] = cfg.password
    cmd = [
        "mysqldump",
        "-h", cfg.host,
        "-P", str(cfg.port),
        "-u", cfg.user,
        "--single-transaction",
        "--routines",
        "--triggers",
        cfg.name,
    ]
    # stdout → файл (mysqldump пишет в stdout)
    return cmd, env


def run_dump_mysql_to_file(cfg: DbConfig, out_sql: Path) -> Path:
    """Обёртка: mysqldump пишет в stdout, перенаправляем в файл."""

    out_sql.parent.mkdir(parents=True, exist_ok=True)
    cmd, env = _mysql_cmd(cfg, out_sql)
    logger.info("dump: mysql → %s", out_sql.name)
    try:
        with out_sql.open("w", encoding="utf-8", newline="\n") as fh:
            result = subprocess.run(
                cmd,
                env=env,
                stdout=fh,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
    except FileNotFoundError as exc:
        raise DumpError("mysqldump не найден в PATH") from exc

    if result.returncode != 0:
        err = (result.stderr or "").strip().replace(cfg.password, "***")
        if out_sql.exists():
            out_sql.unlink(missing_ok=True)
        raise DumpError(f"дамп упал (code={result.returncode}): {err[:500]}")

    if not out_sql.exists() or out_sql.stat().st_size == 0:
        raise DumpError("файл дампа пустой или не создан")

    logger.info("dump ok: %.1f KB", out_sql.stat().st_size / 1024)
    return out_sql


def create_dump(cfg: DbConfig, backup_dir: Path) -> Path:
    out_sql = backup_dir / dump_filename()
    db_type = cfg.db_type.strip().lower()
    if db_type in ("mysql", "mariadb"):
        return run_dump_mysql_to_file(cfg, out_sql)
    return run_dump(cfg, out_sql)
