
from models import Invoice

def get_outstanding(db, shop_id):
    invs = db.query(Invoice).filter(Invoice.shop_id==shop_id).all()
    return sum(i.amount - i.paid_amount for i in invs)
