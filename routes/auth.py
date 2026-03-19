import hashlib

from fastapi import APIRouter, Depends, HTTPException

from database import SessionLocal
from models import AppUser

router = APIRouter()


def db():
    d = SessionLocal()
    try:
        yield d
    finally:
        d.close()


@router.post("/login")
def login(username: str, password: str, database=Depends(db)):
    normalized_username = (username or "").strip().lower()
    password_hash = hashlib.sha256((password or "").encode()).hexdigest()

    user = (
        database.query(AppUser)
        .filter(AppUser.username == normalized_username)
        .filter(AppUser.password_hash == password_hash)
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {
        "username": user.username,
        "role": user.role,
        "label": user.label,
    }
