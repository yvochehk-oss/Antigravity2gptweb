CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sha256 VARCHAR(64) NOT NULL UNIQUE,
    filename VARCHAR(500),
    file_type VARCHAR(50),
    document_type VARCHAR(50),
    file_path TEXT,
    page_count INTEGER DEFAULT 0,
    parser VARCHAR(50),
    raw_text TEXT,
    ocr_confidence NUMERIC(6,5),
    status VARCHAR(50) NOT NULL DEFAULT 'uploaded',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_extractions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    extractor_version VARCHAR(50) NOT NULL DEFAULT 'v3.0',
    model_name VARCHAR(100),
    schema_version VARCHAR(20) NOT NULL DEFAULT '3.0',
    extracted_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(50) NOT NULL DEFAULT 'extracted',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contracts_v3 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id),
    extraction_id UUID REFERENCES document_extractions(id),
    contract_no VARCHAR(200),
    contract_name TEXT,
    party_a_name TEXT,
    party_a_credit_code VARCHAR(18),
    party_b_name TEXT,
    party_b_credit_code VARCHAR(18),
    project_name TEXT,
    sign_date DATE,
    currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
    amount_tax_included NUMERIC(18,2),
    amount_tax_excluded NUMERIC(18,2),
    tax_amount NUMERIC(18,2),
    tax_rate NUMERIC(8,6),
    payment_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
    contract_start_date DATE,
    contract_end_date DATE,
    warranty_period TEXT,
    bank TEXT,
    bank_account TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contracts_v3_contract_no ON contracts_v3(contract_no);
CREATE INDEX IF NOT EXISTS idx_contracts_v3_project_name ON contracts_v3(project_name);

CREATE TABLE IF NOT EXISTS invoices_v3 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id),
    extraction_id UUID REFERENCES document_extractions(id),
    invoice_type VARCHAR(100),
    invoice_code VARCHAR(100),
    invoice_no VARCHAR(100),
    invoice_date DATE,
    buyer_name TEXT,
    buyer_tax_id VARCHAR(18),
    seller_name TEXT,
    seller_tax_id VARCHAR(18),
    amount_excluding_tax NUMERIC(18,2),
    tax_amount NUMERIC(18,2),
    amount_including_tax NUMERIC(18,2),
    tax_rate NUMERIC(8,6),
    currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
    check_code VARCHAR(100),
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_v3_unique
ON invoices_v3(invoice_no, seller_tax_id)
WHERE invoice_no IS NOT NULL AND seller_tax_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS document_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    extraction_id UUID REFERENCES document_extractions(id) ON DELETE CASCADE,
    review_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
