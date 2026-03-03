from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
import asyncio, time
import crud,sqlite3,hashlib,base64
from typing import Dict, Tuple
from typing import Optional
from contextlib import asynccontextmanager, suppress


PING_INTERVAL = 20  # ping 주기 (초)
ALIVE_TIMEOUT = 60    
PING_TIMEOUT = 5    # pong 응답 타임아웃

# ------------------------
# ping/pong 루프 (네이티브 방식)
# ------------------------
async def ping_loop():
    """서버 → 클라이언트 텍스트 ping, 클라 → alive 응답 타임아웃 정리"""
    try:
        while True:
            now = time.time()
            dead_keys = []

            for key, ws in list(clients.items()):
                try:
                    await ws.send_text("ping")
                except Exception as e:
                    print(f"[PING FAIL] {key}: {e}")
                    dead_keys.append(key)
                    continue

                last = last_alive.get(key, 0)
                if now - last > ALIVE_TIMEOUT:
                    print(f"[ALIVE TIMEOUT] {key}")
                    dead_keys.append(key)

            for key in dead_keys:
                ws_to_close = clients.pop(key, None)
                last_alive.pop(key, None)
                if ws_to_close:
                    try:
                        await ws_to_close.close(code=1001)
                    except Exception as e:
                        print(f"[CLOSE FAIL] {key}: {e}")
                print(f"[PING LOOP] cleaned {key}")

            await asyncio.sleep(PING_INTERVAL)
    except asyncio.CancelledError:
        print("[PING LOOP] cancelled")
        raise

# lifespan 핸들러
@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(ping_loop())
    print("[SERVER] ping_loop started")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # 남은 소켓 정리
        for key, ws in list(clients.items()):
            try:
                await ws.close(code=1001)
            except Exception as e:
                print(f"[SERVER SHUTDOWN CLOSE FAIL] {key}: {e}")
        clients.clear()
        last_alive.clear()
        print("[SERVER] ping_loop stopped & sessions cleared")

app = FastAPI(lifespan=lifespan)

app = FastAPI(lifespan=lifespan)
crud.init_db()
crud.migrate_db()
crud.migrate_kiosk_config()
crud.migrate_membership()



# (store_id, table_num) → WebSocket 연결 관리
clients: Dict[Tuple[int, int], WebSocket] = {}
# 마지막 alive 시간 기록
last_alive: Dict[Tuple[int, int], float] = {}
# 이전 active_tables 상태 저장 (자동 open/close 감지용)
# store_id → set of table_num
prev_active_tables: Dict[int, set] = {}
# ADB 연결 상태: store_id → bool (키오스크가 1분마다 POST)
adb_status_store: Dict[int, bool] = {}

# 정적 파일 & 템플릿 설정
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
DB_PATH = "/var/data/kiosk.db" #서버 DB파일 경로


def hash_password(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

# ✅ 로그인 페이지
@app.get("/")
def login_page(request: Request):
    user = request.cookies.get("session_user")
    return templates.TemplateResponse("login.html", {"request": request, "user": user})


# ✅ 회원가입 페이지
@app.get("/register")
def register_page(request: Request):
    user = request.cookies.get("session_user")
    return templates.TemplateResponse("register.html", {"request": request, "user": user})


# ✅ 관리자 페이지

@app.get("/admin")
def admin_page(request: Request):
    import base64

    raw_username = request.cookies.get("session_user")
    raw_name = request.cookies.get("session_name")
    raw_store_id = request.cookies.get("store_id")

    username = base64.b64decode(raw_username).decode("utf-8") if raw_username else None
    name = base64.b64decode(raw_name).decode("utf-8") if raw_name else None

    # 문자열을 정수로 변환 (없으면 None)
    store_id = int(raw_store_id) if raw_store_id is not None else None

    if not username:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "user": name, "store_id": store_id}
    )


# ✅ 로그아웃
@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_user")
    return response


# ✅ 예약 삭제
@app.post("/delete/{rid}")
def delete_reservation(rid: int):
    crud.delete_reservation(rid)
    return RedirectResponse(url="/admin", status_code=303)


# ✅ API
@app.get("/api/reservations")
def api_list(request: Request):
    store_id = request.cookies.get("store_id")
    if not store_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    rows = crud.list_reservations_by_range("1900-01-01", "2100-01-01", int(store_id))
    return {"reservations": rows}



@app.post("/api/reservations")
def api_add(
    store_id: int = Form(...),
    table_num: int = Form(...),
    phone: str = Form(...),
    menu_name: str = Form(...),
    price: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    auth_no: str = Form(...),
):
    crud.add_reservation(store_id,table_num, phone, menu_name, price, start_time, end_time, auth_no)
    return {"status": "ok"}


@app.get("/api/reservations/range")
def api_reservations_range(request: Request, start: str, end: str, store_id: int = None):
    cookie_store_id = request.cookies.get("store_id")
    if not cookie_store_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    cookie_store_id = int(cookie_store_id)

    if cookie_store_id == 0:  # 관리자
        if not store_id:
            return JSONResponse({"error": "관리자는 store_id 필요"}, status_code=400)
        target_store_id = store_id
    else:
        target_store_id = cookie_store_id

    # store_id=4 는 데이터 비공개
    if target_store_id == 4:
        return JSONResponse([])

    rows = crud.list_reservations_by_range(start, end, target_store_id)
    return JSONResponse(rows)


# ✅ 로그인 처리
@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember_me: str = Form(None),
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, password, store_id FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()

    if row and row[2] == hash_password(password):  # ✅ 비밀번호 해시 확인
        user_id, name, _, store_id = row
        response = RedirectResponse(url="/admin", status_code=303)

        # ✅ Base64 인코딩 (쿠키 저장)
        encoded_name = base64.b64encode(name.encode("utf-8")).decode("ascii")
        encoded_username = base64.b64encode(username.encode("utf-8")).decode("ascii")

        cookie_args = {"max_age": 86400} if remember_me else {}

        response.set_cookie("session_user", encoded_username, **cookie_args)
        response.set_cookie("session_name", encoded_name, **cookie_args)
        response.set_cookie("store_id", str(store_id), **cookie_args)   # ✅ store_id 저장

        return response
    else:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "아이디 또는 비밀번호가 올바르지 않습니다."},
        )

# ✅ 회원가입 처리
@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    name: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
):
    if password != password2:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "비밀번호가 일치하지 않습니다.", "user": None},
        )

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users(username, name, password, store_id) VALUES (?, ?, ?, ?)",
            (username, name, hash_password(password), 99),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "이미 존재하는 아이디입니다.", "user": None},
        )
    finally:
        conn.close()

    return RedirectResponse(url="/", status_code=303)

@app.get("/stores")
def store_page(request: Request):

    raw_user = request.cookies.get("session_user")
    if not raw_user:
        return RedirectResponse(url="/", status_code=303)

    # ✅ Base64 디코딩
    try:
        username = base64.b64decode(raw_user).decode("utf-8")
    except Exception:
        return RedirectResponse(url="/", status_code=303)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 로그인 유저 정보 확인
    cur.execute("SELECT id, name, store_id FROM users WHERE username=?", (username,))
    urow = cur.fetchone()

    if not urow or urow[2] != 0:  # 관리자가 아니면 접근 불가
        conn.close()
        return RedirectResponse(url="/admin", status_code=303)

    # 점포 목록
    cur.execute("SELECT id, name, location FROM stores")
    stores = cur.fetchall()

    # 유저 목록 (점포 지정 필요)
    cur.execute("SELECT id, username, name, store_id FROM users")
    users = cur.fetchall()

    conn.close()
    return templates.TemplateResponse(
        "stores.html",
        {"request": request, "user": urow[1], "stores": stores, "users": users}
    )

@app.get("/admin/all_list")
def all_list_page(request: Request):
    store_id = request.cookies.get("store_id")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 점포 목록
    cur.execute("SELECT id, name, location FROM stores")
    stores = cur.fetchall()

    if not store_id or int(store_id) != 0:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse("all_list.html", {"request": request, "user": "관리자", "stores": stores})



@app.post("/save_store")
def save_store(request: Request, id: int = Form(None), name: str = Form(...), location: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if id:  # ID가 있으면 수정
        cur.execute("SELECT id FROM stores WHERE id=?", (id,))
        if cur.fetchone():
            cur.execute("UPDATE stores SET name=?, location=? WHERE id=?", (name, location, id))
            store_id = id
        else:
            cur.execute("INSERT INTO stores (id, name, location) VALUES (?, ?, ?)", (id, name, location))
            store_id = id
    else:  # 새 점포 추가 (자동 증가)
        cur.execute("INSERT INTO stores (name, location) VALUES (?, ?)", (name, location))
        store_id = cur.lastrowid  # 새로 생성된 점포 id 가져오기

    # ✅ 기본 메뉴 자동 추가
    cur.executemany(
        "INSERT INTO store_menus (store_id, menu_name, price, minutes) VALUES (?, ?, ?, ?)",
        [
            (store_id, "30분", 7000, 30),
            (store_id, "60분", 12000, 60),
        ]
    )

    conn.commit()
    conn.close()
    return RedirectResponse(url="/stores", status_code=303)


# ✅ 점포 삭제
@app.post("/delete_store")
def delete_store(request: Request, store_id: int = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 유저가 해당 점포를 사용 중이면 NULL 처리
    cur.execute("UPDATE users SET store_id=NULL WHERE store_id=?", (store_id,))
    cur.execute("DELETE FROM stores WHERE id=?", (store_id,))

    conn.commit()
    conn.close()
    return RedirectResponse(url="/stores", status_code=303)

# ✅ 유저 점포 지정
@app.post("/assign_store")
def assign_store(request: Request, user_id: int = Form(...), store_id: int = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET store_id=? WHERE id=?", (store_id, user_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/stores", status_code=303)

# ✅ 현재 사용 중인 테이블 조회 API
@app.get("/table")
async def api_tables(request: Request):
    store_id = request.cookies.get("store_id")
    if not store_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    store_id = int(store_id)

    # 사용 중인 테이블 (예약/남은 시간)
    rows = crud.list_active_reservations(store_id)
    active_tables = {table: remain for table, remain in rows}
    now = time.time()

    # kiosk_config 에서 테이블 수 읽기 (없으면 4 기본값)
    kc = crud.get_kiosk_config(store_id)
    table_count = kc["table_count"] if kc else 4

    # alive 상태 계산
    alive_tables = {}
    for i in range(1, table_count + 1):
        key = (store_id, i)
        last = last_alive.get(key)
        alive_tables[i] = bool(key in clients and last and (now - last <= ALIVE_TIMEOUT))

    # ADB 연결 상태 (키오스크가 1분마다 POST로 push)
    adb_connected = adb_status_store.get(store_id, None)

    # ✅ 이전 상태와 비교하여 자동 open/close 명령 전송
    current_set = set(active_tables.keys())
    prev_set = prev_active_tables.get(store_id, set())

    # 새로 결제되어 남은시간이 생긴 테이블 → 열기 (10초 간격 순차 전송)
    newly_active = sorted(current_set - prev_set)
    for i, table_num in enumerate(newly_active):
        if i > 0:
            await asyncio.sleep(10)
        print(f"[AUTO OPEN] store={store_id}, table={table_num}")
        await safe_send((store_id, table_num), "open")

    # 남은시간이 사라진 테이블 → 닫기 (10초 간격 순차 전송)
    newly_inactive = sorted(prev_set - current_set)
    for i, table_num in enumerate(newly_inactive):
        if i > 0:
            await asyncio.sleep(10)
        print(f"[AUTO CLOSE] store={store_id}, table={table_num}")
        await safe_send((store_id, table_num), "close")

    # 현재 상태 저장
    prev_active_tables[store_id] = current_set

    return templates.TemplateResponse(
        "table.html",
        {
            "request": request,
            "store_id": store_id,
            "table_count": table_count,
            "active_tables": active_tables,
            "alive_tables": alive_tables,
            "adb_connected": adb_connected,
        }
    )

# 예약삭제
@app.post("/delete_reservation")
def delete_reservation_api(request: Request, rid: int = Form(...)):
    crud.delete_reservation(rid)
    return RedirectResponse(url="/admin/all_list", status_code=303)

# ✅ 점포 메뉴 관리 페이지
@app.get("/store/menus")
def store_menus_page(request: Request):
    store_id = request.cookies.get("store_id")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 스토어 확인
    cur.execute("SELECT id, name FROM stores WHERE id=?", (store_id,))
    store = cur.fetchone()
    if not store:
        conn.close()
        return JSONResponse({"error": "Store not found"}, status_code=404)

    # ✅ 메뉴 조회 (새 컬럼 포함)
    cur.execute("""
        SELECT id, menu_name, price, minutes, always_visible, start_time, end_time, start_date, end_date,
               is_membership, membership_days
        FROM store_menus
        WHERE store_id=?
        ORDER BY minutes
    """, (store_id,))
    menus = cur.fetchall()
    conn.close()

    return templates.TemplateResponse(
        "manage_menus.html",
        {
            "request": request,
            "store_id": store_id,
            "store_name": store[1],
            "menus": menus
        }
    )
    
# ✅ 외부에서 메뉴 조회 (JSON)
@app.get("/api/menus/{store_id}")
def api_get_menus(store_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, menu_name, price, minutes, always_visible, start_time, end_time, start_date, end_date,
               is_membership, membership_days
        FROM store_menus
        WHERE store_id=?
        ORDER BY minutes
    """, (store_id,))
    rows = cur.fetchall()
    conn.close()

    # JSON 형태로 가공
    menus = []
    for r in rows:
        menus.append({
            "id": r[0],
            "menu_name": r[1],
            "price": r[2],
            "minutes": r[3],
            "always_visible": bool(r[4]),
            "start_time": r[5],
            "end_time": r[6],
            "start_date": r[7],
            "end_date": r[8],
            "is_membership": bool(r[9]) if r[9] is not None else False,
            "membership_days": r[10] or 30,
        })

    return {"store_id": store_id, "menus": menus}


# ✅ 새 메뉴 추가
@app.post("/menu/add")
def add_menu(
    request: Request,
    store_id: int = Form(...),
    menu_name: str = Form(...),
    price: int = Form(...),
    minutes: int = Form(...),
    always_visible: Optional[int] = Form(0),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    start_time: Optional[str] = Form(None),
    end_time: Optional[str] = Form(None),
    is_membership: Optional[int] = Form(0),
    membership_days: Optional[int] = Form(30),
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO store_menus
        (store_id, menu_name, price, minutes, always_visible, start_time, end_time, start_date, end_date, is_membership, membership_days)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (store_id, menu_name, price, minutes, always_visible, start_time, end_time, start_date, end_date, is_membership or 0, membership_days or 30)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/store/menus", status_code=303)


# ✅ 메뉴 수정
@app.post("/menu/update")
def update_menu_action(
    request: Request,
    menu_id: int = Form(...),
    menu_name: str = Form(...),
    price: int = Form(...),
    minutes: int = Form(...),
    always_visible: Optional[int] = Form(0),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    start_time: Optional[str] = Form(None),
    end_time: Optional[str] = Form(None),
    is_membership: Optional[int] = Form(0),
    membership_days: Optional[int] = Form(30),
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE store_menus
        SET menu_name=?, price=?, minutes=?, always_visible=?, start_time=?, end_time=?, start_date=?, end_date=?,
            is_membership=?, membership_days=?
        WHERE id=?
        """,
        (menu_name, price, minutes, always_visible, start_time, end_time, start_date, end_date,
         is_membership or 0, membership_days or 30, menu_id)
    )
    conn.commit()

    cur.execute("SELECT store_id FROM store_menus WHERE id=?", (menu_id,))
    row = cur.fetchone()
    conn.close()

    store_id = row[0] if row else 1
    return RedirectResponse(url="/store/menus", status_code=303)


# ✅ 메뉴 삭제
@app.post("/menu/delete")
def delete_menu_action(request: Request, menu_id: int = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT store_id FROM store_menus WHERE id=?", (menu_id,))
    row = cur.fetchone()
    store_id = row[0] if row else 1

    cur.execute("DELETE FROM store_menus WHERE id=?", (menu_id,))
    conn.commit()
    conn.close()

    return RedirectResponse(url="/store/menus", status_code=303)

# ------------------------
# WebSocket 엔드포인트
# ------------------------
@app.websocket("/ws/{store_id}/{table_num}")
async def websocket_endpoint(ws: WebSocket, store_id: int, table_num: int):
    key = (store_id, table_num)
    await ws.accept()

    # 기존 연결이 있으면 닫고 교체
    old = clients.get(key)
    if old and old is not ws:
        try:
            await old.close(code=1012)  # service restarting 등 의미
        except:
            pass


    clients[key] = ws
    last_alive[key] = time.time()
    print(f"[WS CONNECT] {key}")

    try:
        while True:
            data = await ws.receive_text()
            if data == "alive":
                last_alive[key] = time.time()
            else:
                print(f"[WS MSG] {key}: {data}")
    except WebSocketDisconnect as e:
        print(f"[WS DISCONNECT] {key}, code={e.code}, reason={e.reason}")
    except Exception as e:
        print(f"[WS ERROR] {key}: {e}")
    finally:
        # 내가 등록한 소켓일 때만 제거 (경합 안전)
        cur = clients.get(key)
        if cur is ws:
            clients.pop(key, None)
            last_alive.pop(key, None)
        try:
            await ws.close(code=1001)
        except:
            pass
        print(f"[WS CLOSED] {key}")


# ------------------------
# 내부 유틸: 안전 송신
# ------------------------
async def safe_send(key: Tuple[int, int], message: str):
    ws = clients.get(key)
    if not ws:
        print(f"[WS SEND FAIL] {key}: no active client")
        return False
    try:
        await ws.send_text(message)
        return True
    except Exception as e:
        print(f"[WS SEND ERROR] {key}: {e}")
        try:
            await ws.close(code=1001)
        except:
            pass
        # 현재 키의 소켓만 제거
        cur = clients.get(key)
        if cur is ws:
            clients.pop(key, None)
            last_alive.pop(key, None)
        return False
# ------------------------
# REST API: 블라인드 제어
# ------------------------
@app.post("/blind/{store_id}/{table_num}/open")
async def open_blind(store_id: int, table_num: int):
    await safe_send((store_id, table_num), "open")
    return RedirectResponse(url="/table", status_code=303)

@app.post("/blind/{store_id}/{table_num}/close")
async def close_blind(store_id: int, table_num: int):
    await safe_send((store_id, table_num), "close")
    return RedirectResponse(url="/table", status_code=303)




# ══════════════════════════════════════════════════════════════
# 키오스크 설정 관리  (관리자 store_id==0 전용)
# ══════════════════════════════════════════════════════════════

def _require_admin(request: Request):
    """store_id==0 인 관리자만 통과. 아니면 None 반환."""
    raw = request.cookies.get("store_id")
    if raw is None or int(raw) != 0:
        return None
    return True


@app.get("/kiosk/config")
def kiosk_config_page(request: Request):
    """모든 지점의 키오스크 설정 목록 페이지."""
    if not _require_admin(request):
        return RedirectResponse(url="/", status_code=303)

    import sqlite3 as _sq
    conn = _sq.connect(crud.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM stores WHERE id != 0 ORDER BY id")
    stores = cur.fetchall()
    conn.close()

    configs = {c["store_id"]: c for c in crud.list_all_kiosk_configs()}

    return templates.TemplateResponse("kiosk_config.html", {
        "request": request,
        "user": "관리자",
        "store_id": 0,
        "stores": stores,
        "configs": configs,
    })


@app.get("/kiosk/config/{sid}")
def kiosk_config_edit_page(sid: int, request: Request):
    """특정 지점 키오스크 설정 편집 페이지."""
    if not _require_admin(request):
        return RedirectResponse(url="/", status_code=303)

    import sqlite3 as _sq
    conn = _sq.connect(crud.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM stores WHERE id=?", (sid,))
    store = cur.fetchone()
    conn.close()

    if not store:
        return JSONResponse({"error": "Store not found"}, status_code=404)

    cfg = crud.get_kiosk_config(sid) or {
        "store_id": sid, "store_name": store[1],
        "table_count": 4, "blinds_json": "{}",
        "table_reverse": 0,
        "sub_title": "이용권 구매 후 도어락과 블라인드가 금방 열립니다.",
        "support_msg": "", "night_notice": "", "updated_at": "",
    }

    return templates.TemplateResponse("kiosk_config_edit.html", {
        "request": request,
        "user": "관리자",
        "store_id": 0,
        "store": store,
        "cfg": cfg,
    })


@app.post("/kiosk/config/{sid}")
def kiosk_config_save(
    sid: int,
    request: Request,
    store_name:   str = Form(...),
    table_count:  int = Form(...),
    blinds_json:  str = Form("{}"),
    table_reverse: int = Form(0),
    sub_title:    str = Form(""),
    support_msg:  str = Form(""),
    night_notice: str = Form(""),
):
    """키오스크 설정 저장."""
    if not _require_admin(request):
        return RedirectResponse(url="/", status_code=303)

    crud.upsert_kiosk_config(sid, {
        "store_name":    store_name,
        "table_count":   table_count,
        "blinds_json":   blinds_json,
        "table_reverse": table_reverse,
        "sub_title":     sub_title,
        "support_msg":   support_msg,
        "night_notice":  night_notice,
    })
    return RedirectResponse(url="/kiosk/config", status_code=303)


# ── 키오스크 클라이언트용 공개 API ────────────────────────────
@app.get("/api/kiosk/config/{store_id}")
def api_kiosk_config(store_id: int):
    """키오스크 앱이 시작 시 호출 → store_config.json 에 캐싱."""
    cfg = crud.get_kiosk_config(store_id)
    if not cfg:
        return JSONResponse({"error": "config not found"}, status_code=404)
    return cfg


# ── 시간 추가 (서버 DB 반영 + WS 명령) ──────────────────────────────────────
@app.post("/table/{store_id}/{table_num}/add_time")
async def add_time(store_id: int, table_num: int, minutes: int = Form(...)):
    # 1) 서버 DB end_time 연장
    crud.extend_reservation_end_time(store_id, table_num, minutes)
    # 2) 키오스크에 WS 명령 전송 (메모리 active_reservations 연장 + 블라인드 열기)
    await safe_send((store_id, table_num), f"add_time:{minutes}")
    return RedirectResponse(url="/table", status_code=303)


# ── ADB 상태 수신 (키오스크가 1분마다 POST) ──────────────────────────────────
@app.post("/api/adb_status/{store_id}")
async def receive_adb_status(store_id: int, connected: str = Form(...)):
    adb_status_store[store_id] = connected.lower() in ("true", "1", "yes")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════
# 회원권 관리
# ══════════════════════════════════════════════════════════════

def _get_session(request: Request):
    raw_store = request.cookies.get("store_id")
    raw_user  = request.cookies.get("session_name")
    if raw_store is None or raw_user is None:
        return None, None
    try:
        name = base64.b64decode(raw_user).decode("utf-8")
    except Exception:
        name = raw_user
    return int(raw_store), name


@app.get("/memberships")
def memberships_page(request: Request):
    store_id, name = _get_session(request)
    if name is None:
        return RedirectResponse(url="/", status_code=303)

    # 관리자(0) → 전체, 지점 → 해당 지점만
    rows = crud.list_memberships(None if store_id == 0 else store_id)

    # 관리자용 지점 목록 (필터 드롭다운)
    stores = []
    if store_id == 0:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM stores WHERE id != 0 ORDER BY id")
        stores = cur.fetchall()
        conn.close()

    return templates.TemplateResponse("memberships.html", {
        "request":    request,
        "user":       name,
        "store_id":   store_id,
        "memberships": rows,
        "stores":     stores,
    })


@app.post("/membership/add")
def membership_add(
    request: Request,
    store_id:   int = Form(...),
    phone:      str = Form(...),
    menu_name:  str = Form(...),
    start_date: str = Form(...),
    end_date:   str = Form(...),
):
    sess_store, name = _get_session(request)
    if name is None:
        return RedirectResponse(url="/", status_code=303)
    # 지점 계정은 자기 지점만
    if sess_store != 0:
        store_id = sess_store
    crud.add_membership(store_id, phone, menu_name, start_date, end_date)
    return RedirectResponse(url="/memberships", status_code=303)


@app.post("/membership/delete")
def membership_delete(request: Request, mid: int = Form(...)):
    _, name = _get_session(request)
    if name is None:
        return RedirectResponse(url="/", status_code=303)
    crud.delete_membership(mid)
    return RedirectResponse(url="/memberships", status_code=303)


# ── 키오스크용 회원권 확인 API ────────────────────────────────
@app.get("/api/membership/check")
def api_membership_check(store_id: int, phone_last4: str):
    """끝 4자리 + store_id → 유효한 회원권 존재 여부 반환."""
    valid = crud.check_membership_valid(store_id, phone_last4)
    return {"valid": valid}


# ── 키오스크 결제 후 회원권 자동 등록 API ────────────────────
@app.post("/api/membership/register")
def api_membership_register(
    store_id:   int = Form(...),
    phone:      str = Form(...),
    menu_name:  str = Form(...),
    start_date: str = Form(...),
    end_date:   str = Form(...),
):
    mid = crud.add_membership(store_id, phone, menu_name, start_date, end_date)
    return {"status": "ok", "id": mid}
