import hashlib, os, secrets, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import Error, errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

pool = ConnectionPool(os.environ["DATABASE_URL"], kwargs={"row_factory": dict_row}, open=True)
ph = PasswordHasher()                                   # argon2id
DUMMY = ph.hash("dummy-password")                       # выравнивает время ответа при неизвестном email
COOKIE, TTL = "sid", 7 * 24 * 3600
SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"    # локально по http: COOKIE_SECURE=0

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE") and request.headers.get("x-requested-with") != "fetch":
        return JSONResponse({"detail": "Запрос отклонён"}, status_code=403)   # защита от CSRF
    r = await call_next(request)
    r.headers.update({
        "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Strict-Transport-Security": "max-age=31536000",
    })
    return r


class Creds(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    password: str = Field(min_length=10, max_length=128)


class CartItem(BaseModel):
    product_id: int
    qty: int = Field(ge=0, le=20)          # цену клиент не присылает вообще


fails: dict = defaultdict(list)             # при нескольких процессах вынести в Redis


def throttle(key, limit=5, window=300):
    now = time.time()
    fails[key] = [t for t in fails[key] if now - t < window]
    if len(fails[key]) >= limit:
        raise HTTPException(429, "Слишком много попыток, подождите 5 минут")


def start_session(c, uid, resp: Response):
    c.execute("DELETE FROM sessions WHERE expires_at < now()")
    tok = secrets.token_urlsafe(32)
    c.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
              (hashlib.sha256(tok.encode()).digest(), uid, datetime.now(timezone.utc) + timedelta(seconds=TTL)))
    resp.set_cookie(COOKIE, tok, max_age=TTL, httponly=True, secure=SECURE, samesite="lax")


def current_user(request: Request):
    tok = request.cookies.get(COOKIE)
    if not tok:
        raise HTTPException(401, "Нужен вход")
    with pool.connection() as c:
        u = c.execute("""SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id
                         WHERE s.token_hash = %s AND s.expires_at > now()""",
                      (hashlib.sha256(tok.encode()).digest(),)).fetchone()
    if not u:
        raise HTTPException(401, "Нужен вход")
    return u


@app.post("/api/register", status_code=201)
def register(d: Creds, resp: Response):
    with pool.connection() as c:
        try:
            uid = c.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                            (d.email, ph.hash(d.password))).fetchone()["id"]
        except errors.UniqueViolation:
            raise HTTPException(409, "Этот email уже зарегистрирован")
        start_session(c, uid, resp)
    return {"ok": True}


@app.post("/api/login")
def login(d: Creds, request: Request, resp: Response):
    key = (request.client.host, d.email.lower())
    throttle(key)
    with pool.connection() as c:
        u = c.execute("SELECT id, password_hash FROM users WHERE email = %s", (d.email,)).fetchone()
        try:
            ph.verify(u["password_hash"] if u else DUMMY, d.password)
        except VerifyMismatchError:
            u = None
        if not u:
            fails[key].append(time.time())
            raise HTTPException(401, "Неверный email или пароль")
        start_session(c, u["id"], resp)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, resp: Response):
    tok = request.cookies.get(COOKIE)
    if tok:
        with pool.connection() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = %s", (hashlib.sha256(tok.encode()).digest(),))
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(current_user)):
    return u


@app.get("/api/products")
def products():
    with pool.connection() as c:
        return c.execute("SELECT id, name, origin, notes, roast, price_cents, stock FROM products ORDER BY roast, id").fetchall()


@app.get("/api/cart")
def get_cart(u=Depends(current_user)):
    with pool.connection() as c:
        rows = c.execute("""SELECT p.id, p.name, p.price_cents, p.stock, ci.qty FROM cart_items ci
                            JOIN products p ON p.id = ci.product_id WHERE ci.user_id = %s ORDER BY p.id""",
                         (u["id"],)).fetchall()
    return {"items": rows, "total_cents": sum(r["price_cents"] * r["qty"] for r in rows)}


@app.put("/api/cart")
def set_cart(i: CartItem, u=Depends(current_user)):
    with pool.connection() as c:
        if i.qty == 0:
            c.execute("DELETE FROM cart_items WHERE user_id = %s AND product_id = %s", (u["id"], i.product_id))
        else:
            try:
                c.execute("""INSERT INTO cart_items (user_id, product_id, qty) VALUES (%s, %s, %s)
                             ON CONFLICT (user_id, product_id) DO UPDATE SET qty = EXCLUDED.qty""",
                          (u["id"], i.product_id, i.qty))
            except errors.ForeignKeyViolation:
                raise HTTPException(404, "Товар не найден")
    return {"ok": True}


@app.post("/api/checkout")
def checkout(u=Depends(current_user)):
    with pool.connection() as c:
        try:
            oid = c.execute("SELECT place_order(%s) AS id", (u["id"],)).fetchone()["id"]
        except Error as e:
            if e.sqlstate == "SH001":
                raise HTTPException(400, "Корзина пуста")
            if e.sqlstate == "SH002":
                raise HTTPException(409, "Часть товара уже раскупили. Проверьте корзину")
            raise
    return {"order_id": oid}


@app.get("/api/orders")
def orders(u=Depends(current_user)):
    with pool.connection() as c:       # user_id берётся из сессии, чужие заказы запросить нельзя
        return c.execute("""SELECT o.id, o.total_cents, o.created_at,
              (SELECT json_agg(json_build_object('name', p.name, 'qty', i.qty, 'price_cents', i.price_cents))
                 FROM order_items i JOIN products p ON p.id = i.product_id WHERE i.order_id = o.id) AS items
              FROM orders o WHERE o.user_id = %s ORDER BY o.id DESC LIMIT 20""", (u["id"],)).fetchall()


app.mount("/", StaticFiles(directory="static", html=True))
