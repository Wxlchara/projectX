-- Package 1 owns this schema. JSON is validated with shared Pydantic models
-- before persistence by feature services. Timestamps are UTC ISO 8601 strings.
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE INDEX sessions_expiry ON sessions(expires_at);

CREATE TABLE profiles (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    data_json TEXT NOT NULL CHECK (json_valid(data_json)),
    updated_at TEXT NOT NULL
);

CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scenario_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    profile_revision INTEGER NOT NULL CHECK (profile_revision > 0),
    ruleset_version TEXT NOT NULL,
    trip_json TEXT NOT NULL CHECK (json_valid(trip_json)),
    answers_json TEXT NOT NULL CHECK (json_valid(answers_json)),
    -- NULL means not projected yet; it must never be treated as ready.
    projection_json TEXT CHECK (projection_json IS NULL OR json_valid(projection_json)),
    stale INTEGER NOT NULL DEFAULT 1 CHECK (stale IN (0, 1)),
    stale_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, owner_id)
);
CREATE INDEX cases_owner ON cases(owner_id);

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_type TEXT NOT NULL CHECK (document_type IN ('passport', 'bank_statement', 'insurance')),
    storage_key TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL CHECK (mime_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    extraction_state TEXT NOT NULL DEFAULT 'uploaded'
        CHECK (extraction_state IN ('uploaded', 'extracted', 'needs_input', 'failed')),
    facts_revision INTEGER NOT NULL DEFAULT 1 CHECK (facts_revision > 0),
    fields_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(fields_json)),
    quality_findings_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(quality_findings_json)),
    raw_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, owner_id)
);
CREATE INDEX documents_owner ON documents(owner_id);

CREATE TABLE case_documents (
    case_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    attached_at TEXT NOT NULL,
    PRIMARY KEY (case_id, document_id),
    FOREIGN KEY (case_id, owner_id) REFERENCES cases(id, owner_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, owner_id) REFERENCES documents(id, owner_id) ON DELETE CASCADE
);
CREATE INDEX case_documents_document ON case_documents(document_id);

CREATE TABLE checklist_actions (
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    input_signature_json TEXT NOT NULL CHECK (json_valid(input_signature_json)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (case_id, item_id)
);

CREATE TABLE check_results (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    evaluated_at TEXT NOT NULL,
    input_signature_json TEXT NOT NULL CHECK (json_valid(input_signature_json)),
    findings_json TEXT NOT NULL CHECK (json_valid(findings_json)),
    stale INTEGER NOT NULL DEFAULT 1 CHECK (stale IN (0, 1))
);
CREATE INDEX checks_case ON check_results(case_id, evaluated_at);

CREATE TABLE routes (
    case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    route_json TEXT NOT NULL CHECK (json_valid(route_json)),
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    input_signature_json TEXT CHECK (input_signature_json IS NULL OR json_valid(input_signature_json)),
    stale INTEGER NOT NULL DEFAULT 1 CHECK (stale IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE letters (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type = 'cover_letter'),
    language TEXT NOT NULL DEFAULT 'en' CHECK (language = 'en'),
    content TEXT NOT NULL,
    missing_facts_json TEXT NOT NULL CHECK (json_valid(missing_facts_json)),
    generation_mode TEXT NOT NULL CHECK (generation_mode IN ('ai', 'template')),
    from_cache INTEGER NOT NULL CHECK (from_cache IN (0, 1)),
    input_signature_json TEXT NOT NULL CHECK (json_valid(input_signature_json)),
    stale INTEGER NOT NULL DEFAULT 1 CHECK (stale IN (0, 1)),
    edited_by_user INTEGER NOT NULL DEFAULT 0 CHECK (edited_by_user IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX letters_case ON letters(case_id);

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'form_prepared', 'appointment_booked', 'submitted', 'under_review',
        'additional_documents_requested', 'decision_received',
        'passport_returned', 'entry_preparation_completed'
    )),
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source = 'manual'),
    note TEXT,
    outcome TEXT CHECK (outcome IS NULL OR outcome IN ('unknown', 'approved', 'refused', 'not_applicable'))
);
CREATE INDEX events_case ON events(case_id, occurred_at);

-- Foreign keys remove links but services must also remove binaries, rebuild
-- projections and invalidate check/letter/route snapshots on fact changes.
