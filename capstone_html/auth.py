import hashlib
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path("users.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id   TEXT UNIQUE,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            name          TEXT,
            department    TEXT,
            position      TEXT,
            email         TEXT,
            status        TEXT DEFAULT '활성'
        )
    """)
    conn.commit()
    # 엑셀로 임포트된 데이터가 없을 때만 기본 계정 생성
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        _create_user(conn, "admin", "admin123", "admin")
        _create_user(conn, "user",  "user123",  "user")
    conn.close()


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def _create_user(conn, username: str, password: str, role: str,
                 employee_id: str = None, name: str = None,
                 department: str = None, position: str = None,
                 email: str = None, status: str = "활성"):
    salt = secrets.token_hex(16)
    conn.execute(
        """INSERT OR REPLACE INTO users
           (employee_id, username, password_hash, salt, role, name, department, position, email, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (employee_id, username, _hash(password, salt), salt, role,
         name, department, position, email, status),
    )
    conn.commit()


def verify_user(username: str, password: str) -> dict | None:
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND status = '활성'", (username,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if secrets.compare_digest(_hash(password, row["salt"]), row["password_hash"]):
        return {
            "username":   row["username"],
            "role":       row["role"],
            "name":       row["name"],
            "department": row["department"],
        }
    return None
