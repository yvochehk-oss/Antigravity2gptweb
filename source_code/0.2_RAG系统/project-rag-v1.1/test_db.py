from app.db import SessionLocal
from app.models import Document
db = SessionLocal()
for name in ["TF-A08-D01_重型塔式起重机及附着式升降脚手架租赁合同_盖章原件影印本.jpg", "TF-A08-D01_重型塔式起重机及附着式升降脚手架租赁合同.pdf"]:
    doc = db.query(Document).filter(Document.filename == name).first()
    if doc:
        print(f"Name: {name}")
        print(f"Entity: {doc.entity_code}")
        print(f"Counterparty: {doc.counterparty_code}")
        print(f"Type: {doc.document_type}")
        print(f"Category: {doc.business_category}")
        print("---")
