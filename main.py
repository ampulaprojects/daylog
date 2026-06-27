from fastapi import FastAPI, HTTPException, BackgroundTasks, Cookie, Depends, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
from database import (
    init_db, create_user, get_user_by_username, get_user_by_id,
    set_user_role, update_user_password,
    create_entry, get_entries, get_entry
)
from auth import verify_password, create_session_token, decode_session_token
import json

app = FastAPI()

SESSION_MAX_AGE = 30 * 24 * 3600


def _init_users():
    if not get_user_by_username("jan"):
        create_user("jan", "jan2026", role="admin")
    else:
        set_user_role("jan", "admin")
    create_user("katka", "katka2026", role="user")


@app.on_event("startup")
def startup():
    init_db()
    _init_users()


# ── Auth helpers ────────────────────────────────────────────────────────────

def get_session_user(session: Optional[str] = Cookie(None)):
    if not session:
        return None
    user_id = decode_session_token(session)
    if not user_id:
        return None
    return get_user_by_id(user_id)


def require_auth(user=Depends(get_session_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Nie si prihlásený")
    return user


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/")
def root(session: Optional[str] = Cookie(None)):
    if not (decode_session_token(session) if session else None):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/index.html")


@app.get("/login")
def login_page():
    return FileResponse("static/login.html")


@app.get("/profile")
def profile_page(session: Optional[str] = Cookie(None)):
    if not (decode_session_token(session) if session else None):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/profile.html")


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return RedirectResponse(url="/login?error=1", status_code=302)
    token = create_session_token(user["id"])
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie("session", token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session")
    return resp


@app.get("/me")
def me(user=Depends(require_auth)):
    return {"username": user["username"], "role": user["role"]}


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


@app.post("/change-password")
def change_password(body: ChangePassword, user=Depends(require_auth)):
    if not verify_password(body.old_password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Nesprávne staré heslo")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Heslo musí mať aspoň 6 znakov")
    update_user_password(user["id"], body.new_password)
    return {"ok": True}


# ── Entries ────────────────────────────────────────────────────────────────────

class EntryCreate(BaseModel):
    entry_date: str
    text: str
    title: Optional[str] = None
    mood: Optional[str] = None
    tags: Optional[List[str]] = []
    source: Optional[str] = "typed"


@app.post("/entries", status_code=201)
def add_entry(entry: EntryCreate, background_tasks: BackgroundTasks, user=Depends(require_auth)):
    entry_id = create_entry(
        entry_date=entry.entry_date,
        text=entry.text,
        title=entry.title,
        mood=entry.mood,
        tags=entry.tags,
        source=entry.source,
        user_id=user["id"]
    )
    return {"id": entry_id}


@app.get("/entries")
def list_entries(
    search: Optional[str] = None,
    mood: Optional[str] = None,
    limit: int = 50,
    user=Depends(require_auth)
):
    entries = get_entries(search=search, mood=mood, limit=limit, user_id=user["id"])
    for e in entries:
        e["tags"] = json.loads(e["tags"] or "[]")
        e["llm_tags"] = json.loads(e["llm_tags"] or "[]")
    return entries


@app.get("/entries/{entry_id}")
def get_single_entry(entry_id: int, user=Depends(require_auth)):
    entry = get_entry(entry_id, user_id=user["id"])
    if not entry:
        raise HTTPException(status_code=404, detail="Záznam nenájdený")
    entry["tags"] = json.loads(entry["tags"] or "[]")
    entry["llm_tags"] = json.loads(entry["llm_tags"] or "[]")
    return entry


app.mount("/static", StaticFiles(directory="static"), name="static")
