import sqlite3
from typing import Dict, List

from fastapi import FastAPI, HTTPException

app = FastAPI()


def get_db():
    conn = sqlite3.connect("contracts.db")
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/opportunities")
def list_opportunities() -> List[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM opportunities")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@app.get("/opportunities/{notice_id}")
def get_opportunity(notice_id: str) -> Dict:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM opportunities WHERE notice_id = ?", (notice_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


@app.get("/search")
def search(q: str) -> List[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM opportunities WHERE title LIKE ?", (f"%{q}%",))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
