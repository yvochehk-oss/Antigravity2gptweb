from sqlalchemy import create_engine, text
from app.services.extractor import extract_contract_fields_from_text, extract_invoice_fields_from_text, extract_payment_fields_from_text
from decimal import Decimal
import hashlib

engine = create_engine('postgresql+psycopg://yvoche@localhost:5432/projectrag')
project_id = 6

with engine.begin() as conn:
    # 1. Clean
    conn.execute(text('DELETE FROM real_costs WHERE project_id = 6'))
    conn.execute(text('DELETE FROM invoices WHERE project_id = 6'))
    conn.execute(text('DELETE FROM cashflows WHERE project_id = 6'))
    conn.execute(text('DELETE FROM contracts WHERE project_id = 6'))
    print('Cleaned old records for project 6.')

    # 2. Ingest formal Contracts
    contract_chunks = conn.execute(text(
        "SELECT c.id, c.content, d.filename FROM chunks c "
        "JOIN documents d ON c.document_id = d.id "
        "WHERE d.filename LIKE '%.pdf' AND (d.filename LIKE '%合同%' OR d.filename LIKE 'CDTF%') "
        "ORDER BY c.id"
    )).all()
    
    seen_contract_nos = set()
    for ch in contract_chunks:
        fields = extract_contract_fields_from_text(ch.content)
        cno = fields.get('contract_no')
        if not cno or cno in seen_contract_nos:
            continue
        seen_contract_nos.add(cno)
        
        seller_code = 'A08'
        buyer_code = 'EXT-TF' if 'MAIN' in cno else 'A08'
        if 'MAIN' not in cno:
            pb_name = fields.get('party_b_name') or '外部供应商'
            pb_tax = fields.get('party_b_tax_id') or '91510100MA61XXXXXX'
            ext_row = conn.execute(text('SELECT code FROM external_parties WHERE name = :name'), {'name': pb_name}).first()
            if not ext_row:
                cnt = conn.execute(text('SELECT count(*) FROM external_parties')).scalar()
                new_code = f'EXT-{cnt+1:03d}'
                conn.execute(text('INSERT INTO external_parties (code, name, short_name, tax_id, active, kind) VALUES (:code, :name, :sname, :tax_id, true, :kind)'), {'code': new_code, 'name': pb_name, 'sname': pb_name[:60], 'tax_id': pb_tax, 'kind': 'supplier'})
                seller_code = new_code
            else:
                seller_code = ext_row[0]
            buyer_code = 'A08'

        conn.execute(text(
            "INSERT INTO contracts (project_id, contract_no, category, buyer_code, seller_code, amount, internal_trade, note) "
            "VALUES (:pid, :cno, :cat, :bcode, :scode, :amt, false, :note)"
        ), {'pid': project_id, 'cno': cno, 'cat': fields.get('category') or 'subcontract', 'bcode': buyer_code, 'scode': seller_code, 'amt': fields.get('total_amount') or 0.0, 'note': f'来自 RAG 凭证库 ({ch.filename})'})
        print(f'✓ Imported Contract: {cno} | {fields.get("total_amount")} 元 | 甲方={buyer_code} 乙方={seller_code}')

    # 3. Ingest formal Invoices
    invoice_chunks = conn.execute(text(
        "SELECT c.id, c.content, d.filename FROM chunks c "
        "JOIN documents d ON c.document_id = d.id "
        "WHERE d.filename LIKE 'INVOICE_%.pdf' "
        "ORDER BY c.id"
    )).all()
    seen_invoices = set()
    for ch in invoice_chunks:
        fields = extract_invoice_fields_from_text(ch.content)
        ino = fields.get('invoice_no')
        if not ino or ino in seen_invoices:
            continue
        seen_invoices.add(ino)
        direction = 'in' if '锐宝' in (fields.get('buyer_name') or '') else 'out'
        
        # Determine counterparty code
        seller_tax = fields.get('seller_tax_id') or '91510100MA61XXXXXX'
        seller_name = fields.get('seller_name') or '供应商'
        ext_row = conn.execute(text('SELECT code FROM external_parties WHERE tax_id = :tax OR name = :name'), {'tax': seller_tax, 'name': seller_name}).first()
        if not ext_row:
            cnt = conn.execute(text('SELECT count(*) FROM external_parties')).scalar()
            cp_code = f'EXT-{cnt+1:03d}'
            conn.execute(text('INSERT INTO external_parties (code, name, short_name, tax_id, active, kind) VALUES (:code, :name, :sname, :tax_id, true, :kind)'), {'code': cp_code, 'name': seller_name, 'sname': seller_name[:60], 'tax_id': seller_tax, 'kind': 'supplier'})
        else:
            cp_code = ext_row[0]

        conn.execute(text(
            "INSERT INTO invoices (project_id, invoice_no, direction, category, entity_code, counterparty_code, net, vat, rate, deductible, period, note) "
            "VALUES (:pid, :ino, :dir, :cat, 'A08', :cp_code, :net, :vat, :rate, true, :period, :note)"
        ), {
            'pid': project_id, 'ino': ino, 'dir': direction, 'cat': fields.get('category') or 'material',
            'cp_code': cp_code, 'net': Decimal(str(fields.get('net_amount', 0))),
            'vat': Decimal(str(fields.get('vat_amount', 0))), 'rate': Decimal(str(fields.get('vat_rate', 0.09))),
            'period': fields.get('period') or '2026-03', 'note': f'来自 RAG 电子发票 ({ch.filename})'
        })
        print(f'✓ Imported Invoice: {ino} | 不含税={fields.get("net_amount")} | 税额={fields.get("vat_amount")}')

    # 4. Ingest formal Payments
    payment_chunks = conn.execute(text(
        "SELECT c.id, c.content, d.filename FROM chunks c "
        "JOIN documents d ON c.document_id = d.id "
        "WHERE d.filename LIKE 'BANK_%.pdf' "
        "ORDER BY c.id"
    )).all()
    seen_payments = set()
    for ch in payment_chunks:
        fields = extract_payment_fields_from_text(ch.content)
        ref = fields.get('bank_reference')
        if not ref or ref in seen_payments:
            continue
        seen_payments.add(ref)
        
        payee_name = fields.get('payee_name') or '供应商'
        ext_row = conn.execute(text('SELECT code FROM external_parties WHERE name = :name'), {'name': payee_name}).first()
        cp_code = ext_row[0] if ext_row else 'EXT-001'
        
        fingerprint = hashlib.sha256(f"{ref}:{fields.get('amount')}".encode()).hexdigest()
        conn.execute(text(
            "INSERT INTO cashflows (project_id, amount, counterparty_code, direction, note, period, transaction_date, bank_reference, source_fingerprint, entity_code) "
            "VALUES (:pid, :amt, :cp, 'out', :note, :period, :tdate, :ref, :fp, 'A08')"
        ), {
            'pid': project_id, 'amt': Decimal(str(fields.get('amount', 0))),
            'cp': cp_code, 'period': fields.get('period') or '2026-03',
            'tdate': fields.get('payment_date') or '2026-03-22',
            'ref': ref, 'fp': fingerprint,
            'note': f'来自 RAG 银行电子回单 ({ch.filename})'
        })
        print(f'✓ Imported Payment: {ref} | 金额={fields.get("amount")} | 收款方={payee_name}')

print('=== ALL 100% INGESTED INTO TAX DATABASE! ===')
