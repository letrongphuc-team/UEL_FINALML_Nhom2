import os
from pathlib import Path

import mysql.connector
import pandas as pd


def load_project_env():
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in os.environ:
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value


load_project_env()


class Connector:
    def __init__(
        self,
        server=None,
        port=None,
        database=None,
        username=None,
        password=None,
    ):
        self.server = server or os.getenv("MYSQL_HOST", "localhost")
        self.port = int(port or os.getenv("MYSQL_PORT", "3306"))
        self.database = database or os.getenv("MYSQL_DATABASE", "haircare_shop")
        self.username = username or os.getenv("MYSQL_USER", "root")
        self.password = password if password is not None else os.getenv("MYSQL_PASSWORD", "")
        self.conn = None

    def connect(self):
        if self.conn is not None:
            return self.conn
        self.conn = mysql.connector.connect(
            host=self.server,
            port=self.port,
            database=self.database,
            user=self.username,
            password=self.password,
            use_pure=True,
            connection_timeout=int(os.getenv("MYSQL_CONNECT_TIMEOUT", "10")),
        )
        return self.conn

    def queryDataset(self, sql, params=None):
        if self.conn is None:
            self.connect()
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            return pd.DataFrame(cursor.fetchall(), columns=cursor.column_names)
        finally:
            cursor.close()

    def execute(self, sql, params=None):
        if self.conn is None:
            self.connect()
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            self.conn.commit()
            return cursor.rowcount
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
