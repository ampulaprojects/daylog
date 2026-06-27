from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from database import init_db, create_entry, get_entries, get_entry
import json

app = FastAPI()

@app.on_event("startup")
def startup():
    init_db()

@app.get("/")
def root():
    return FileResponse("static/index.html")

class EntryCreate(BaseModel):
    entry_date: str
    text: str
    title: Optional[str] = None
    mood: Optional[str] = None
    tags: Optional[List[str]] = []
    source: Optional[str] = "typed"

@app.post("/entries", status_code=201)
def add_entry(entry: EntryCreate, background_tasks: BackgroundTasks):
    entry_id = create_entry(
        entry_date=entry.entry_date,
        text=entry.text,
        title=entry.title,
        mood=entry.mood,
        tags=entry.tags,
        source=entry.source
    )
    return {"id": entry_id}

@app.get("/entries")
def list_entries(search: Optional[str] = None, mood: Optional[str] = None, limit: int = 50):
    entries = get_entries(search=search, mood=mood, limit=limit)
    for e in entries:
        e["tags"] = json.loads(e["tags"] or "[]")
        e["llm_tags"] = json.loads(e["llm_tags"] or "[]")
    return entries

@app.get("/entries/{entry_id}")
def get_single_entry(entry_id: int):
    entry = get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Záznam nenájdený")
    entry["tags"] = json.loads(entry["tags"] or "[]")
    entry["llm_tags"] = json.loads(entry["llm_tags"] or "[]")
    return entry

app.mount("/static", StaticFiles(directory="static"), name="static")
