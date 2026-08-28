import re
from pathlib import Path
from app.services.metadata import infer_from_filename, load_canonical_entity_cache

def test(name):
    print("Testing:", name)
    res = infer_from_filename(name, load_canonical_entity_cache())
    print("Entity:", res.get("entity_code"), res.get("entity_resolution_status"))
    print("Counterparty:", res.get("counterparty_code"), res.get("counterparty_resolution_status"))
    print("Type:", res.get("document_type"))
    print("Category:", res.get("business_category"))
    print("---")

test("TF-A08-D01_重型塔式起重机及附着式升降脚手架租赁合同_盖章原件影印本.jpg")
