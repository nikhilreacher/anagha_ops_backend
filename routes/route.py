
from fastapi import APIRouter, Depends
from database import SessionLocal
from models import Route, Shop

router = APIRouter()

def db():
    d=SessionLocal()
    try: yield d
    finally: d.close()

@router.get("/")
def get_routes(database=Depends(db)):
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
