"""A tiny API to try tapedeck on.   Run:  uvicorn examples.sample_api.app:app --port 8000"""

import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="sample api")

ITEMS = {1: {"id": 1, "name": "keyboard", "price": 49.0}, 2: {"id": 2, "name": "mouse", "price": 19.5}}


class NewItem(BaseModel):
    name: str
    price: float


@app.get("/items")
def list_items(page: int = 1):
    return {"page": page, "items": list(ITEMS.values())}


@app.get("/items/{item_id}")
def get_item(item_id: int):
    if item_id not in ITEMS:
        raise HTTPException(404, "no such item")
    return ITEMS[item_id]


@app.post("/items", status_code=201)
def create_item(item: NewItem):
    new_id = max(ITEMS) + 1
    ITEMS[new_id] = {"id": new_id, **item.model_dump()}
    return ITEMS[new_id]


@app.get("/slow")
async def slow(ms: int = 200):
    await asyncio.sleep(ms / 1000)
    return {"waited_ms": ms}


@app.get("/time")
def now():
    """Changes on every call: a good example of a 'noisy' value."""
    return {"now": datetime.now(UTC).isoformat()}


@app.post("/login")
def login(body: dict):
    return {"access_token": "super-secret-token", "token_type": "bearer", "user": body.get("email")}
