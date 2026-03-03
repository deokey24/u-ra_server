import sqlite3
from datetime import date,datetime, timedelta
import hashlib
import os
from dotenv import load_dotenv




DB_PATH = "/var/data/kiosk.db" #서버 DB파일 경로

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

def migrate_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    migrations = [
        "ALTER TABLE store_menus ADD COLUMN always_visible INTEGER DEFAULT 1",
        "ALTER TABLE store_menus ADD COLUMN start_time TEXT",  # HH:MM
        "ALTER TABLE store_menus ADD COLUMN end_time TEXT",    # HH:MM
        "ALTER TABLE store_menus ADD COLUMN start_date TEXT",  # YYYY-MM-DD
        "ALTER TABLE store_menus ADD COLUMN end_date TEXT"     # YYYY-MM-DD
    ]

    for sql in migrations:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError:
            pass

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
             AND auth_no IS NOT NULL 
             AND auth_no != ''
             AND auth_no != 'None'
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
        """
        SELECT id, menu_name, price, minutes, always_visible, start_date, end_date, start_time, end_time
        FROM store_menus
        WHERE store_id = ?
        ORDER BY minutes, always_visible DESC, id
        """,
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


# ─────────────────────────────────────────────────────────────
# kiosk_config CRUD
# ─────────────────────────────────────────────────────────────

def migrate_kiosk_config():
    """kiosk_config 테이블 생성 (없을 때만)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kiosk_config (
            store_id        INTEGER PRIMARY KEY,
            store_name      TEXT    DEFAULT \'\',
            table_count     INTEGER DEFAULT 4,
            blinds_json     TEXT    DEFAULT \'{}\',
            table_reverse   INTEGER DEFAULT 0,
            sub_title       TEXT    DEFAULT \'이용권 구매 후 도어락과 블라인드가 금방 열립니다.\',
            support_msg     TEXT    DEFAULT \'\',
            night_notice    TEXT    DEFAULT \'\',
            updated_at      TEXT    DEFAULT \'\'
        )
    """)
    conn.commit()
    conn.close()


def get_kiosk_config(store_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM kiosk_config WHERE store_id=?", (store_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["store_id","store_name","table_count","blinds_json",
            "table_reverse","sub_title","support_msg","night_notice","updated_at"]
    return dict(zip(cols, row))


def upsert_kiosk_config(store_id: int, data: dict):
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO kiosk_config
            (store_id, store_name, table_count, blinds_json, table_reverse,
             sub_title, support_msg, night_notice, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(store_id) DO UPDATE SET
            store_name    = excluded.store_name,
            table_count   = excluded.table_count,
            blinds_json   = excluded.blinds_json,
            table_reverse = excluded.table_reverse,
            sub_title     = excluded.sub_title,
            support_msg   = excluded.support_msg,
            night_notice  = excluded.night_notice,
            updated_at    = excluded.updated_at
    """, (
        store_id,
        data.get("store_name", ""),
        data.get("table_count", 4),
        data.get("blinds_json", "{}"),
        int(data.get("table_reverse", 0)),
        data.get("sub_title", "이용권 구매 후 도어락과 블라인드가 금방 열립니다."),
        data.get("support_msg", ""),
        data.get("night_notice", ""),
        datetime.now().isoformat(timespec="seconds"),
    ))
    conn.commit()
    conn.close()


def list_all_kiosk_configs():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT k.*, s.name as store_display_name
        FROM kiosk_config k
        LEFT JOIN stores s ON s.id = k.store_id
        ORDER BY k.store_id
    """)
    rows = cur.fetchall()
    conn.close()
    cols = ["store_id","store_name","table_count","blinds_json",
            "table_reverse","sub_title","support_msg","night_notice",
            "updated_at","store_display_name"]
    return [dict(zip(cols, r)) for r in rows]


def extend_reservation_end_time(store_id: int, table_num: int, minutes: int) -> bool:
    """현재 활성 예약의 end_time 을 minutes 분 연장. 성공 여부 반환."""
    from datetime import datetime, timedelta
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
    # 현재 활성 예약 1건 조회
    cur.execute("""
        SELECT id, end_time FROM reservations
        WHERE store_id=? AND table_num=?
          AND strftime('%s', substr(replace(start_time,'T',' '),1,19)) <= strftime('%s',?)
          AND strftime('%s', substr(replace(end_time,'T',' '),1,19))   >  strftime('%s',?)
        ORDER BY end_time DESC LIMIT 1
    """, (store_id, table_num, now, now))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    rid, end_time_str = row
    # end_time 파싱 후 연장
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            end_dt = datetime.strptime(end_time_str[:19], fmt)
            break
        except ValueError:
            continue
    else:
        conn.close()
        return False
    new_end = (end_dt + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE reservations SET end_time=? WHERE id=?", (new_end, rid))
    conn.commit()
    conn.close()
    return True
