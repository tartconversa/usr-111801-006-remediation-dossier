import sqlite3


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, jurisdiction TEXT NOT NULL,
    classification TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fields (
    source_id TEXT NOT NULL REFERENCES sources(id), name TEXT NOT NULL,
    meaning TEXT NOT NULL, kind TEXT NOT NULL, sensitive INTEGER NOT NULL,
    PRIMARY KEY(source_id,name)
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    schema_version INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
    received_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    recipient TEXT NOT NULL, purpose TEXT NOT NULL, valid_until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS policies (
    jurisdiction TEXT NOT NULL, classification TEXT NOT NULL, purpose TEXT NOT NULL,
    decision TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(jurisdiction,classification,purpose)
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,
    PRIMARY KEY(id,revision)
);
CREATE TABLE IF NOT EXISTS product_inputs (
    product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    PRIMARY KEY(product_id,product_revision,asset_id),
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    recipient TEXT NOT NULL, purpose TEXT NOT NULL, created_at TEXT NOT NULL,
    input_digest TEXT NOT NULL, policy_revisions TEXT NOT NULL,
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rect_cases (
    id TEXT PRIMARY KEY, scenic_area TEXT NOT NULL, notice TEXT NOT NULL,
    opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rect_requirements (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES rect_cases(id),
    seq INTEGER NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
    UNIQUE(case_id,seq)
);
CREATE TABLE IF NOT EXISTS submission_batches (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES rect_cases(id),
    submitted_at TEXT NOT NULL, submitter TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS requirement_materials (
    id TEXT NOT NULL, requirement_id TEXT NOT NULL REFERENCES rect_requirements(id),
    version INTEGER NOT NULL, batch_id TEXT NOT NULL REFERENCES submission_batches(id),
    payload TEXT NOT NULL, digest TEXT NOT NULL, submitted_at TEXT NOT NULL,
    superseded INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(requirement_id,version)
);
CREATE TABLE IF NOT EXISTS rect_reviews (
    id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL REFERENCES rect_requirements(id),
    material_version INTEGER NOT NULL, reviewer TEXT NOT NULL, decision TEXT NOT NULL,
    reason TEXT NOT NULL, reviewed_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT, revoke_reason TEXT,
    FOREIGN KEY(requirement_id,material_version)
        REFERENCES requirement_materials(requirement_id,version)
);
CREATE TABLE IF NOT EXISTS rect_publications (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES rect_cases(id),
    created_at TEXT NOT NULL, publisher TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'draft',
    snapshot TEXT NOT NULL, digest TEXT NOT NULL
);
"""


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection
