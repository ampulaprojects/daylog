import io
import json
import pathlib
import re
from datetime import datetime
from fastapi import FastAPI, HTTPException, Cookie, Depends, Form, UploadFile, File, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
from database import (
    init_db, get_user_by_username, get_user_by_id,
    update_user_password,
    create_entry, get_entries, get_entry, create_event,
    delete_entry, update_entry_text, replace_entry_events,
    get_medications, create_medication, update_medication,
    delete_medication, set_medication_active, reorder_medications,
    get_catalog, get_catalog_item, create_catalog_item, update_catalog_item,
    delete_catalog_item, set_catalog_active, find_by_alias
)
from auth import verify_password, create_session_token, decode_session_token
from llm import extract_events, transcribe_photo, scan_med_package

UPLOAD_DIR = pathlib.Path("uploads")
app = FastAPI()

SESSION_MAX_AGE = 30 * 24 * 3600


@app.on_event("startup")
def startup():
    init_db()


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


@app.get("/meds")
def meds_page(session: Optional[str] = Cookie(None)):
    if not (decode_session_token(session) if session else None):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/meds.html")


# Note: GET /catalog is defined once, in the Medications/catalog section below —
# it serves the HTML page for browsers and JSON for API clients (Accept header).


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
    entry_time: Optional[str] = None
    source: Optional[str] = "typed"


class ExtractRequest(BaseModel):
    text: str
    entry_date: str
    entry_time: Optional[str] = None


class EventItem(BaseModel):
    event_time: Optional[str] = None
    event_type: str
    value: str
    note: Optional[str] = None
    catalog_id: Optional[int] = None


class ConfirmRequest(BaseModel):
    entry_date: str
    entry_time: Optional[str] = None
    text: str
    source: Optional[str] = "typed"
    events: List[EventItem]
    photo_path: Optional[str] = None
    llm_raw: Optional[str] = None
    llm_model: Optional[str] = None


@app.post("/entries/transcribe")
async def entries_transcribe(file: UploadFile = File(...), user=Depends(require_auth)):
    UPLOAD_DIR.mkdir(exist_ok=True)
    contents = await file.read()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{user['username']}.jpg"
    orig_path = UPLOAD_DIR / filename
    with open(orig_path, "wb") as f:
        f.write(contents)
    try:
        result = transcribe_photo(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chyba prepisu: {e}")
    return {
        "transcription": result["transcription"],
        "suggested_date": result.get("suggested_date"),
        "photo_path": f"uploads/{filename}",
    }


# split na oddeľovače množstva — ASCII 'x'/'X' zámerne NIE (rozbil by napr. "Chlorprotixen")
_MED_TOKEN_SPLIT = re.compile(r"[\s,;/×·]+")


def _enrich_with_catalog(events: list) -> None:
    """Pre každý liekový event doplní catalog_id / catalog_name / matched.
    Hodnota (value) sa NEPREPISUJE — je to audit toho, čo bolo reálne povedané."""
    for ev in events:
        if ev.get("event_type") != "liek":
            ev["catalog_id"] = None
            ev["catalog_name"] = None
            ev["matched"] = None
            continue
        candidates = []
        med_name = (ev.get("med_name") or "").strip()
        if med_name:
            candidates.append(med_name)
        for tok in _MED_TOKEN_SPLIT.split(ev.get("value") or ""):
            tok = tok.strip()
            if tok and tok not in candidates:
                candidates.append(tok)
        match = None
        for cand in candidates:
            match = find_by_alias(cand)
            if match:
                break
        ev["catalog_id"] = match["id"] if match else None
        ev["catalog_name"] = match["canonical_name"] if match else None
        ev["matched"] = bool(match)


@app.post("/entries/extract")
def entries_extract(body: ExtractRequest, user=Depends(require_auth)):
    try:
        events, cleaned_text, llm_raw, llm_model = extract_events(body.text, body.entry_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM chyba: {e}")
    _enrich_with_catalog(events)
    return {"events": events, "cleaned_text": cleaned_text, "llm_raw": llm_raw, "llm_model": llm_model}


@app.post("/entries/confirm", status_code=201)
def entries_confirm(body: ConfirmRequest, user=Depends(require_auth)):
    llm_processed_at = datetime.utcnow().isoformat() if body.llm_raw else None
    entry_id = create_entry(
        entry_date=body.entry_date,
        text=body.text,
        entry_time=body.entry_time,
        source=body.source,
        user_id=user["id"],
        photo_path=body.photo_path,
        llm_analysis=body.llm_raw,
        llm_model=body.llm_model,
        llm_processed_at=llm_processed_at,
    )
    for ev in body.events:
        create_event(
            entry_id=entry_id,
            user_id=user["id"],
            event_type=ev.event_type,
            value=ev.value,
            event_time=ev.event_time,
            note=ev.note,
            catalog_id=ev.catalog_id if ev.event_type == "liek" else None,
        )
    return {"entry_id": entry_id, "event_count": len(body.events)}


class EntryUpdate(BaseModel):
    text: str
    events: List[EventItem]
    entry_date: Optional[str] = None
    entry_time: Optional[str] = None


@app.delete("/entries/{entry_id}", status_code=204)
def delete_entry_endpoint(entry_id: int, user=Depends(require_auth)):
    delete_entry(entry_id)


@app.put("/entries/{entry_id}")
def update_entry_endpoint(entry_id: int, body: EntryUpdate, user=Depends(require_auth)):
    update_entry_text(entry_id, body.text, body.entry_date, body.entry_time)
    events_data = [{"event_time": e.event_time, "event_type": e.event_type,
                    "value": e.value, "note": e.note,
                    "catalog_id": e.catalog_id if e.event_type == "liek" else None}
                   for e in body.events]
    replace_entry_events(entry_id, user["id"], events_data)
    return {"entry_id": entry_id, "event_count": len(body.events)}


@app.post("/entries", status_code=201)
def add_entry(entry: EntryCreate, user=Depends(require_auth)):
    entry_id = create_entry(
        entry_date=entry.entry_date,
        text=entry.text,
        entry_time=entry.entry_time,
        source=entry.source,
        user_id=user["id"]
    )
    return {"id": entry_id}


@app.get("/entries")
def list_entries(
    search: Optional[str] = None,
    limit: int = 50,
    user=Depends(require_auth)
):
    return get_entries(search=search, limit=limit, with_events=True)


@app.get("/entries/{entry_id}")
def get_single_entry(entry_id: int, user=Depends(require_auth)):
    entry = get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Záznam nenájdený")
    return entry


_SAFE_FILENAME = re.compile(r'^[a-zA-Z0-9_\-]+\.(jpg|jpeg|png)$')


@app.get("/photos/{filename}")
def get_photo(filename: str, user=Depends(require_auth)):
    if not _SAFE_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Neplatné meno súboru")
    path = (UPLOAD_DIR / filename).resolve()
    if not str(path).startswith(str(UPLOAD_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Neplatná cesta")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Súbor nenájdený")
    return FileResponse(path, media_type="image/jpeg")


# ── Medications ──────────────────────────────────────────────────────────────

class MedBody(BaseModel):
    name: str
    kind: str = "liek"
    count: Optional[float] = None
    dose: Optional[str] = None
    unit: Optional[str] = None
    time_type: Optional[str] = None
    time_exact: Optional[str] = None
    time_value: Optional[str] = None
    days: str = "kazdy_den"
    note: Optional[str] = None
    sort_order: int = 0


@app.get("/medications")
def list_medications(include_inactive: bool = False, user=Depends(require_auth)):
    return get_medications(include_inactive=include_inactive)


@app.post("/medications", status_code=201)
def add_medication(body: MedBody, user=Depends(require_auth)):
    med_id = create_medication(
        name=body.name, kind=body.kind, count=body.count, dose=body.dose,
        unit=body.unit, time_type=body.time_type, time_exact=body.time_exact,
        time_value=body.time_value, days=body.days, note=body.note,
        sort_order=body.sort_order
    )
    return {"id": med_id}


class ReorderItem(BaseModel):
    id: int
    sort_order: int


@app.put("/medications/reorder")
def reorder_meds_endpoint(items: List[ReorderItem], user=Depends(require_auth)):
    reorder_medications([(item.id, item.sort_order) for item in items])
    return {"ok": True}


@app.put("/medications/{med_id}")
def edit_medication(med_id: int, body: MedBody, user=Depends(require_auth)):
    update_medication(
        med_id=med_id, name=body.name, kind=body.kind, count=body.count,
        dose=body.dose, unit=body.unit, time_type=body.time_type,
        time_exact=body.time_exact, time_value=body.time_value,
        days=body.days, note=body.note, sort_order=body.sort_order
    )
    return {"id": med_id}


@app.delete("/medications/{med_id}", status_code=204)
def remove_medication(med_id: int, user=Depends(require_auth)):
    delete_medication(med_id)


@app.patch("/medications/{med_id}/active")
def toggle_med_active(med_id: int, active: bool, user=Depends(require_auth)):
    set_medication_active(med_id, active)
    return {"id": med_id, "active": active}


# ── Med catalog (referenčná príručka) ────────────────────────────────────────

class CatalogBody(BaseModel):
    canonical_name: str
    aliases: List[str] = []
    kind: str = "liek"
    strength: Optional[str] = None
    form: Optional[str] = None
    manufacturer: Optional[str] = None
    sukl_code: Optional[str] = None
    atc_code: Optional[str] = None
    description: Optional[str] = None
    side_effects: Optional[str] = None
    personal_notes: Optional[str] = None
    info_source: Optional[str] = None
    photo_path: Optional[str] = None
    photos: List[str] = []
    extracted_raw: Optional[str] = None


def _catalog_out(item: dict) -> dict:
    """Parse the aliases/photos JSON columns into real lists for the client."""
    try:
        item["aliases"] = json.loads(item.get("aliases") or "[]")
    except (ValueError, TypeError):
        item["aliases"] = []
    try:
        item["photos"] = json.loads(item.get("photos") or "[]")
    except (ValueError, TypeError):
        item["photos"] = []
    # spätná kompatibilita: ak má liek len photo_path a prázdne photos, zobraz ho v galérii
    if not item["photos"] and item.get("photo_path"):
        item["photos"] = [item["photo_path"]]
    return item


@app.get("/catalog")
def catalog_root(include_inactive: bool = False,
                 session: Optional[str] = Cookie(None),
                 accept: str = Header("")):
    """Browsers (Accept: text/html) get the page; API clients that send
    Accept: application/json get the JSON list. Both require auth."""
    authed = bool(decode_session_token(session)) if session else False
    if "application/json" in accept.lower():
        if not authed:
            raise HTTPException(status_code=401, detail="Nie si prihlásený")
        return [_catalog_out(i) for i in get_catalog(include_inactive=include_inactive)]
    if not authed:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/catalog.html")


def _resolve_photos(body: CatalogBody):
    """Zjednoť photos (galéria) a photo_path (hlavná/thumbnail).
    Hlavná musí byť v galérii; ak galéria prázdna, použi photo_path."""
    photos = list(body.photos or [])
    photo_path = body.photo_path
    if photo_path and photo_path not in photos:
        photos.insert(0, photo_path)
    if not photo_path and photos:
        photo_path = photos[0]
    return photo_path, json.dumps(photos)


@app.post("/catalog", status_code=201)
def add_catalog(body: CatalogBody, user=Depends(require_auth)):
    photo_path, photos_json = _resolve_photos(body)
    item_id = create_catalog_item(
        canonical_name=body.canonical_name, aliases=json.dumps(body.aliases),
        kind=body.kind, strength=body.strength, form=body.form,
        manufacturer=body.manufacturer, sukl_code=body.sukl_code,
        atc_code=body.atc_code, description=body.description,
        side_effects=body.side_effects, personal_notes=body.personal_notes,
        info_source=body.info_source, photo_path=photo_path, photos=photos_json,
        extracted_raw=body.extracted_raw
    )
    return {"id": item_id}


@app.get("/catalog/lookup")
def catalog_lookup(name: str, user=Depends(require_auth)):
    item = find_by_alias(name)
    if not item:
        return {"match": None}
    return {"match": _catalog_out(item)}


@app.post("/catalog/scan")
async def catalog_scan(files: List[UploadFile] = File(...), user=Depends(require_auth)):
    """Odfotené krabičky lieku (jedna alebo viac strán) → Claude vision prečíta
    a zlúči údaje. Nič neukladá do katalógu — vráti len návrh polí + cesty k fotkám."""
    UPLOAD_DIR.mkdir(exist_ok=True)
    photos = []
    images = []
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    for i, file in enumerate(files):
        contents = await file.read()
        filename = f"{ts}_med_{user['username']}_{i}.jpg"
        with open(UPLOAD_DIR / filename, "wb") as f:
            f.write(contents)
        photos.append(f"uploads/{filename}")
        images.append(contents)
    try:
        result = scan_med_package(images)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chyba skenu: {e}")
    extracted = result.get("extracted_all") or {}
    return {
        "fields": result["fields"],
        "extracted_all": extracted,
        "extracted_raw": json.dumps(extracted, ensure_ascii=False) if extracted else None,
        "raw": result["raw"],
        "photos": photos,
        "photo_path": photos[0] if photos else None,
    }


@app.put("/catalog/{item_id}")
def edit_catalog(item_id: int, body: CatalogBody, user=Depends(require_auth)):
    if not get_catalog_item(item_id):
        raise HTTPException(status_code=404, detail="Položka nenájdená")
    photo_path, photos_json = _resolve_photos(body)
    update_catalog_item(
        item_id=item_id, canonical_name=body.canonical_name,
        aliases=json.dumps(body.aliases), kind=body.kind, strength=body.strength,
        form=body.form, manufacturer=body.manufacturer, sukl_code=body.sukl_code,
        atc_code=body.atc_code, description=body.description,
        side_effects=body.side_effects, personal_notes=body.personal_notes,
        info_source=body.info_source, photo_path=photo_path, photos=photos_json,
        extracted_raw=body.extracted_raw
    )
    return {"id": item_id}


@app.delete("/catalog/{item_id}", status_code=204)
def remove_catalog(item_id: int, user=Depends(require_auth)):
    delete_catalog_item(item_id)


@app.patch("/catalog/{item_id}/active")
def toggle_catalog_active(item_id: int, active: bool, user=Depends(require_auth)):
    set_catalog_active(item_id, active)
    return {"id": item_id, "active": active}


app.mount("/static", StaticFiles(directory="static"), name="static")
