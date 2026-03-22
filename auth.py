"""auth.py — Password hashing, JWT tokens, user auth helpers."""
from __future__ import annotations
import datetime, os, secrets
from fastapi import Request
from jose import jwt, JWTError
from passlib.context import CryptContext
from db import get_conn, now_iso

SECRET_KEY   = os.getenv("SECRET_KEY", secrets.token_hex(32))
ALGORITHM    = "HS256"
TOKEN_EXPIRE = 60 * 24 * 7   # minutes

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(p: str) -> str:    return pwd_ctx.hash(p)
def verify_password(p: str, h: str) -> bool: return pwd_ctx.verify(p, h)

def create_token(uid: str) -> str:
    exp = datetime.datetime.utcnow() + datetime.timedelta(minutes=TOKEN_EXPIRE)
    return jwt.encode({"sub": uid, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:   return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError: return None

def get_current_user(request: Request):
    token = request.cookies.get("sm_token")
    if not token: return None
    uid = decode_token(token)
    if not uid:   return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None

def require_user(request: Request):
    """FastAPI dependency — returns user or raises 307."""
    from fastapi import HTTPException
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user

def update_last_login(uid: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (now_iso(), uid))
