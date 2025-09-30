from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
import crud,sqlite3,hashlib,base64
from typing import Dict, Tuple

app = FastAPI()
crud.init_db()


# (store_id, table_num) → WebSocket 연결 관리
clients: Dict[Tuple[int, int], WebSocket] = {}

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
def api_tables(request: Request):
    store_id = request.cookies.get("store_id")
    if not store_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    rows = crud.list_active_reservations(int(store_id))
    active_tables = {table: remain for table, remain in rows}  # 미리 dict로 변환

    return templates.TemplateResponse(
        "table.html",
        {"request": request, "active_tables": active_tables, "store_id": store_id}   # 반드시 request 전달 필요
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
    cur.execute("SELECT id, name FROM stores WHERE id=?", (store_id,))
    store = cur.fetchone()

    if not store:
        conn.close()
        return JSONResponse({"error": "Store not found"}, status_code=404)

    cur.execute("SELECT id, menu_name, price, minutes FROM store_menus WHERE store_id=? ORDER BY minutes", (store_id,))
    menus = cur.fetchall()
    conn.close()

    return templates.TemplateResponse(
        "manage_menus.html",
        {"request": request, "store_id": store_id, "store_name": store[1], "menus": menus}
    )


# ✅ 새 메뉴 추가
@app.post("/menu/add")
def add_menu(
    request: Request,
    store_id: int = Form(...),
    menu_name: str = Form(...),
    price: int = Form(...),
    minutes: int = Form(...)
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO store_menus (store_id, menu_name, price, minutes) VALUES (?, ?, ?, ?)",
        (store_id, menu_name, price, minutes)
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
    minutes: int = Form(...)
):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE store_menus SET menu_name=?, price=?, minutes=? WHERE id=?", (menu_name, price, minutes, menu_id))
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
async def websocket_endpoint(websocket: WebSocket, store_id: int, table_num: int):
    key = (store_id, table_num)
    await websocket.accept()
    clients[key] = websocket
    print(f"[WS CONNECT] store={store_id}, table={table_num}")

    try:
        await websocket.wait_closed()
    finally:
        print(f"[WS DISCONNECT] store={store_id}, table={table_num}")
        clients.pop(key, None)  # 안전하게 삭제

# ------------------------
# REST API: 블라인드 제어
# ------------------------
@app.post("/blind/{store_id}/{table_num}/open")
async def open_blind(store_id: int, table_num: int):
    key = (store_id, table_num)
    if key in clients:
        await clients[key].send_text("open")
    return RedirectResponse(url="/table", status_code=303)


@app.post("/blind/{store_id}/{table_num}/close")
async def close_blind(store_id: int, table_num: int):
    key = (store_id, table_num)
    if key in clients:
        await clients[key].send_text("close")
    return RedirectResponse(url="/table", status_code=303)

