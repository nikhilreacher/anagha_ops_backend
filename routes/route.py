
from fastapi import APIRouter, Depends
from database import SessionLocal
from models import Route, Shop

router = APIRouter()
VALID_BUSINESS_TYPES = {"mainline", "icd"}

def db():
    d=SessionLocal()
    try: yield d
    finally: d.close()


def normalize_business_type(value: str | None) -> str:
    normalized = (value or "mainline").strip().lower()
    if normalized not in VALID_BUSINESS_TYPES:
        return "mainline"
    return normalized

@router.get("/")
def get_routes(business_type: str = "mainline", database=Depends(db)):
    normalized_business_type = normalize_business_type(business_type)
    routes = database.query(Route).all()
    if routes:
        return [
            {
                "id": f"beat{r.id}",
                "name": f"Beat {r.id}",
                "route_name": r.name,
                "beat_value": r.name,
            }
            for r in routes
        ]

    shop_beats = (
        database.query(Shop.beat)
        .filter(Shop.beat.isnot(None))
        .filter(Shop.beat != "")
        .filter(Shop.business_type == normalized_business_type)
        .distinct()
        .order_by(Shop.beat)
        .all()
    )
    return [
        {
            "id": f"beat{index}",
            "name": f"Beat {index}",
            "route_name": beat,
            "beat_value": beat,
        }
        for index, (beat,) in enumerate(shop_beats, start=1)
    ]
