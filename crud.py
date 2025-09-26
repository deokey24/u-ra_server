import sqlite3
from datetime import date,datetime, timedelta
import hashlib
import os
from dotenv import load_dotenv




DB_PATH = "kiosk.db"

load_dotenv()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_NAME = os.getenv("ADMIN_NAME", "관리자")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin1234")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS reservations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER,   -- ✅ 점포 구분
            table_num INTEGER,
            phone TEXT,
            menu_name TEXT,
            price INTEGER,
            start_time TEXT,
            end_time TEXT,
            auth_no TEXT
        )'''
    )
    
    # 사용자 테이블
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            name TEXT,
            password TEXT,
            store_id INTEGER
        )'''
    )


    # 점포 테이블 (추후 확장용)
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS stores(
            id INTEGER PRIMARY KEY,
            name TEXT,
            location TEXT,
            table_count INTEGER DEFAULT 4
        )'''
    )

    # 점포 메뉴 테이블
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS store_menus(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER,
            menu_name TEXT,
            price INTEGER,
            minutes INTEGER
        )'''
    )

    # 0번 관리자용 점포 생성
    cur.execute(
        "INSERT OR IGNORE INTO stores (id, name, location) VALUES (0, '관리자용', '-')"
    )

    # 관리자 계정 생성
    cur.execute(
        "INSERT OR IGNORE INTO users (username, name, password, store_id) VALUES (?, ?, ?, ?)",
        (
            ADMIN_USERNAME,
            ADMIN_NAME,
            hash_password(ADMIN_PASSWORD),
            0
        )
    )


    conn.commit()
    conn.close()


def add_reservation(store_id, table_num, phone, menu_name, price, start_time, end_time, auth_no):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reservations(store_id, table_num, phone, menu_name, price, start_time, end_time, auth_no) VALUES(?,?,?,?,?,?,?,?)",
        (store_id, table_num, phone, menu_name, price, start_time, end_time, auth_no),
    )
    conn.commit()
    conn.close()


def list_reservations():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, table_num, phone, menu_name, price, start_time, end_time, auth_no FROM reservations ORDER BY start_time DESC"
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def delete_reservation(rid):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM reservations WHERE id = ?", (rid,))
    conn.commit()
    conn.close()

def list_reservations_by_range(start_date: str, end_date: str, store_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, table_num, phone, menu_name, price,
                  strftime('%Y-%m-%d %H:%M:%S', start_time),
                  strftime('%Y-%m-%d %H:%M:%S', end_time),
                  auth_no
           FROM reservations
           WHERE date(start_time) BETWEEN ? AND ?
             AND store_id = ?
           ORDER BY start_time DESC""",
        (start_date, end_date, store_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def list_today_reservations():
    today = date.today().isoformat()
    return list_reservations_by_range(today, today)

# 사용중인 테이블 조회 (남은 시간 분 단위 계산)
def list_active_reservations(store_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 서버 현재 날짜/시간
    today = date.today()  # 예: 2025-09-27

    # UTC기준이라 한국 시간으로 수정
    now = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")

    query = """
        SELECT table_num,
               (strftime('%s', substr(replace(end_time, 'T', ' '), 1, 19))
                - strftime('%s', ?)) / 60 AS remaining_minutes,
               start_time, end_time
        FROM reservations
        WHERE store_id = ?
          AND strftime('%s', substr(replace(start_time, 'T', ' '), 1, 19)) <= strftime('%s', ?)
          AND strftime('%s', substr(replace(end_time, 'T', ' '), 1, 19)) > strftime('%s', ?)
    """

    cur.execute(query, (now, store_id, now, now))
    rows = cur.fetchall()
    print(now)
    conn.close()

    return [(r[0], int(r[1])) for r in rows]


#메뉴 조회
def get_menus(store_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, menu_name, price, minutes FROM store_menus WHERE store_id = ? ORDER BY minutes",
        (store_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def update_menu(menu_id: int, name: str, price: int, minutes: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE store_menus SET menu_name = ?, price = ?, minutes = ? WHERE id = ?",
        (name, price, minutes, menu_id)
    )
    conn.commit()
    conn.close()

def delete_menu(menu_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM store_menus WHERE id = ?", (menu_id,))
    conn.commit()
    conn.close()
