-- ============================================================================
-- DH Builder: Datacenter Hall Construction Management
-- PostgreSQL 16 — Initial Schema Migration
-- ============================================================================
-- Multi-tenant SaaS with RLS, RBAC, approval workflow, realtime locks.
-- Coordinates are cell-based integers (col, row), not pixels.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- 1. TENANTS
-- ============================================================================
CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE tenants IS 'Root entity for multi-tenant isolation. Every data row traces back to a tenant.';

CREATE UNIQUE INDEX idx_tenants_slug ON tenants (slug);

-- ============================================================================
-- 2. USERS
-- ============================================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    password_hash   TEXT,
    display_name    TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'VIEWER'
                        CHECK (role IN ('ADMIN','PM','MANAGER','TECH','VIEWER')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    invite_token    TEXT,
    invite_expires  TIMESTAMPTZ,
    invited_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE users IS 'User accounts scoped to a tenant. Role determines RBAC permissions. password_hash is NULL until invitation is accepted.';

CREATE UNIQUE INDEX idx_users_tenant_email ON users (tenant_id, email);
CREATE INDEX idx_users_tenant ON users (tenant_id);
CREATE INDEX idx_users_invite_token ON users (invite_token) WHERE invite_token IS NOT NULL;

-- ============================================================================
-- 3. REFRESH TOKENS
-- ============================================================================
CREATE TABLE refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    device_info TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE refresh_tokens IS 'Hashed refresh tokens for JWT rotation. Revoked tokens kept for audit trail.';

CREATE UNIQUE INDEX idx_refresh_tokens_hash ON refresh_tokens (token_hash);
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens (user_id);
CREATE INDEX idx_refresh_tokens_active ON refresh_tokens (expires_at) WHERE revoked_at IS NULL;

-- ============================================================================
-- 4. HALLS
-- ============================================================================
CREATE TABLE halls (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    code            TEXT NOT NULL,
    color           TEXT NOT NULL DEFAULT '#3B82F6',
    cols            INT NOT NULL CHECK (cols > 0 AND cols <= 200),
    rows            INT NOT NULL CHECK (rows > 0 AND rows <= 200),
    axis_x_name     TEXT NOT NULL DEFAULT 'Column',
    axis_y_name     TEXT NOT NULL DEFAULT 'Row',
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','archived')),
    archived_at     TIMESTAMPTZ,
    archived_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE halls IS 'Datacenter halls with flat grid layout (cols x rows). Axes have customizable names.';

CREATE UNIQUE INDEX idx_halls_tenant_code ON halls (tenant_id, code);
CREATE INDEX idx_halls_tenant ON halls (tenant_id);
CREATE INDEX idx_halls_tenant_status ON halls (tenant_id, status);

-- ============================================================================
-- 5. RACK TYPES (per hall)
-- ============================================================================
CREATE TABLE rack_types (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hall_id     UUID NOT NULL REFERENCES halls(id) ON DELETE CASCADE,
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE rack_types IS 'Rack type definitions per hall. Determines which processes apply to racks of this type.';

CREATE UNIQUE INDEX idx_rack_types_hall_name ON rack_types (hall_id, name);
CREATE INDEX idx_rack_types_tenant ON rack_types (tenant_id);
CREATE INDEX idx_rack_types_hall ON rack_types (hall_id);

-- ============================================================================
-- 6. PROCESSES (per hall)
-- ============================================================================
CREATE TABLE processes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hall_id     UUID NOT NULL REFERENCES halls(id) ON DELETE CASCADE,
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE processes IS 'Work stages within a hall (e.g. "Power Installation", "Optical Fiber"). Assigned to racks via rack_types.';

CREATE UNIQUE INDEX idx_processes_hall_name ON processes (hall_id, name);
CREATE INDEX idx_processes_tenant ON processes (tenant_id);
CREATE INDEX idx_processes_hall_order ON processes (hall_id, sort_order);

-- ============================================================================
-- 7. RACK TYPE <-> PROCESS (M:N)
-- ============================================================================
CREATE TABLE rack_type_processes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rack_type_id    UUID NOT NULL REFERENCES rack_types(id) ON DELETE CASCADE,
    process_id      UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    sort_order      INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE rack_type_processes IS 'Links rack types to processes. When a rack gets a type, it inherits these processes.';

CREATE UNIQUE INDEX idx_rtp_type_process ON rack_type_processes (rack_type_id, process_id);
CREATE INDEX idx_rtp_tenant ON rack_type_processes (tenant_id);
CREATE INDEX idx_rtp_process ON rack_type_processes (process_id);

-- ============================================================================
-- 8. PROCESS STATUSES (ordered, per process)
-- ============================================================================
CREATE TABLE process_statuses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id          UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    color               TEXT NOT NULL DEFAULT '#6B7280',
    sort_order          INT NOT NULL DEFAULT 0,
    is_done             BOOLEAN NOT NULL DEFAULT FALSE,
    requires_comment    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE process_statuses IS 'Ordered status options per process. is_done=true marks terminal success state for progress calculation.';

CREATE UNIQUE INDEX idx_process_statuses_proc_name ON process_statuses (process_id, name);
CREATE INDEX idx_process_statuses_tenant ON process_statuses (tenant_id);
CREATE INDEX idx_process_statuses_proc_order ON process_statuses (process_id, sort_order);

-- ============================================================================
-- 9. STATUS TEMPLATES (reusable presets, per tenant)
-- ============================================================================
CREATE TABLE status_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE status_templates IS 'Reusable status presets per tenant. PM can save a set of statuses as template and apply to new processes.';

CREATE UNIQUE INDEX idx_status_templates_tenant_name ON status_templates (tenant_id, name);
CREATE INDEX idx_status_templates_tenant ON status_templates (tenant_id);

-- ============================================================================
-- 10. STATUS TEMPLATE ITEMS
-- ============================================================================
CREATE TABLE status_template_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id         UUID NOT NULL REFERENCES status_templates(id) ON DELETE CASCADE,
    process_name        TEXT NOT NULL,
    status_name         TEXT NOT NULL,
    color               TEXT NOT NULL DEFAULT '#6B7280',
    sort_order          INT NOT NULL DEFAULT 0,
    is_done             BOOLEAN NOT NULL DEFAULT FALSE,
    requires_comment    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE status_template_items IS 'Individual status entries within a template. process_name is text (not FK) for portability across halls.';

CREATE INDEX idx_sti_template ON status_template_items (template_id);
CREATE INDEX idx_sti_template_process ON status_template_items (template_id, process_name, sort_order);

-- ============================================================================
-- 11. GROUPS (containers on canvas, formerly SU)
-- ============================================================================
CREATE TABLE groups (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hall_id             UUID NOT NULL REFERENCES halls(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                TEXT NOT NULL DEFAULT '',
    pos_col             INT NOT NULL CHECK (pos_col >= 0),
    pos_row             INT NOT NULL CHECK (pos_row >= 0),
    width               INT NOT NULL CHECK (width > 0),
    height              INT NOT NULL CHECK (height > 0),
    orientation         TEXT NOT NULL DEFAULT 'horizontal'
                            CHECK (orientation IN ('horizontal','vertical')),
    label_preset        TEXT NOT NULL DEFAULT 'top-left'
                            CHECK (label_preset IN (
                                'top-left','top-center','top-right',
                                'bottom-left','bottom-center','bottom-right',
                                'custom'
                            )),
    label_offset_col    INT NOT NULL DEFAULT 0,
    label_offset_row    INT NOT NULL DEFAULT 0,
    main_rack_id        UUID,
    color               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE groups IS 'Movable containers on the hall canvas (replaces SU from old schema). Position and size in grid cells.';

CREATE INDEX idx_groups_hall ON groups (hall_id);
CREATE INDEX idx_groups_tenant ON groups (tenant_id);
CREATE INDEX idx_groups_hall_pos ON groups (hall_id, pos_col, pos_row);

-- ============================================================================
-- 12. RACKS
-- ============================================================================
CREATE TABLE racks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hall_id         UUID NOT NULL REFERENCES halls(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    rack_type_id    UUID REFERENCES rack_types(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    pos_col         INT CHECK (pos_col >= 0),
    pos_row         INT CHECK (pos_row >= 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE racks IS 'Individual racks. Standalone racks have pos_col/pos_row set. Grouped racks have position via group_racks junction.';

CREATE INDEX idx_racks_hall ON racks (hall_id);
CREATE INDEX idx_racks_tenant ON racks (tenant_id);
CREATE INDEX idx_racks_hall_type ON racks (hall_id, rack_type_id);
CREATE INDEX idx_racks_hall_pos ON racks (hall_id, pos_col, pos_row)
    WHERE pos_col IS NOT NULL AND pos_row IS NOT NULL;

-- FK for main_rack_id on groups (deferred due to circular reference)
ALTER TABLE groups
    ADD CONSTRAINT fk_groups_main_rack
    FOREIGN KEY (main_rack_id) REFERENCES racks(id) ON DELETE SET NULL;

-- ============================================================================
-- 13. GROUP_RACKS (M:N junction with local position)
-- ============================================================================
CREATE TABLE group_racks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    rack_id     UUID NOT NULL REFERENCES racks(id) ON DELETE CASCADE,
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    local_col   INT NOT NULL CHECK (local_col >= 0),
    local_row   INT NOT NULL CHECK (local_row >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE group_racks IS 'M:N junction: rack can belong to multiple groups, each with its own local position within the group.';

CREATE UNIQUE INDEX idx_group_racks_pair ON group_racks (group_id, rack_id);
CREATE UNIQUE INDEX idx_group_racks_pos ON group_racks (group_id, local_col, local_row);
CREATE INDEX idx_group_racks_rack ON group_racks (rack_id);
CREATE INDEX idx_group_racks_tenant ON group_racks (tenant_id);

-- ============================================================================
-- 14. RACK PROCESS STATES (current state + approval workflow)
-- ============================================================================
CREATE TABLE rack_process_states (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rack_id             UUID NOT NULL REFERENCES racks(id) ON DELETE CASCADE,
    process_id          UUID NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    status_id           UUID NOT NULL REFERENCES process_statuses(id) ON DELETE RESTRICT,
    approval_status     TEXT NOT NULL DEFAULT 'APPROVED'
                            CHECK (approval_status IN ('PENDING','APPROVED','REJECTED')),
    updated_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at         TIMESTAMPTZ,
    previous_status_id  UUID REFERENCES process_statuses(id) ON DELETE SET NULL,
    note                TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE rack_process_states IS 'Current state of each process on each rack. Tracks approval workflow. previous_status_id used for revert on REJECT.';

CREATE UNIQUE INDEX idx_rps_rack_process ON rack_process_states (rack_id, process_id);
CREATE INDEX idx_rps_tenant ON rack_process_states (tenant_id);
CREATE INDEX idx_rps_rack ON rack_process_states (rack_id);
CREATE INDEX idx_rps_status ON rack_process_states (status_id);
CREATE INDEX idx_rps_pending ON rack_process_states (tenant_id, approval_status)
    WHERE approval_status = 'PENDING';

-- ============================================================================
-- 15. RACK PROCESS STATE HISTORY (audit log, insert-only)
-- ============================================================================
CREATE TABLE rack_process_state_history (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rack_process_state_id   UUID NOT NULL REFERENCES rack_process_states(id) ON DELETE CASCADE,
    rack_id                 UUID NOT NULL,
    process_id              UUID NOT NULL,
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    old_status_id           UUID REFERENCES process_statuses(id) ON DELETE SET NULL,
    new_status_id           UUID NOT NULL REFERENCES process_statuses(id) ON DELETE RESTRICT,
    approval_status         TEXT NOT NULL
                                CHECK (approval_status IN ('PENDING','APPROVED','REJECTED')),
    changed_by              UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_by             UUID REFERENCES users(id) ON DELETE SET NULL,
    note                    TEXT NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE rack_process_state_history IS 'Immutable audit log of every status change on every rack process. Never updated, only inserted.';

CREATE INDEX idx_rpsh_state ON rack_process_state_history (rack_process_state_id);
CREATE INDEX idx_rpsh_rack ON rack_process_state_history (rack_id);
CREATE INDEX idx_rpsh_tenant ON rack_process_state_history (tenant_id);
CREATE INDEX idx_rpsh_tenant_time ON rack_process_state_history (tenant_id, created_at DESC);
CREATE INDEX idx_rpsh_changed_by ON rack_process_state_history (changed_by) WHERE changed_by IS NOT NULL;

-- ============================================================================
-- 16. OBJECT LOCKS (realtime collaboration)
-- ============================================================================
CREATE TABLE object_locks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    hall_id     UUID NOT NULL REFERENCES halls(id) ON DELETE CASCADE,
    object_type TEXT NOT NULL CHECK (object_type IN ('rack','group','hall_settings')),
    object_id   UUID NOT NULL,
    locked_by   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE object_locks IS 'Temporary editing locks for realtime collaboration. Auto-expire via expires_at.';

CREATE UNIQUE INDEX idx_object_locks_object ON object_locks (object_type, object_id);
CREATE INDEX idx_object_locks_tenant ON object_locks (tenant_id);
CREATE INDEX idx_object_locks_hall ON object_locks (hall_id);
CREATE INDEX idx_object_locks_expires ON object_locks (expires_at);
CREATE INDEX idx_object_locks_user ON object_locks (locked_by);


-- ============================================================================
-- ROW-LEVEL SECURITY
-- ============================================================================
-- Convention: application sets current_setting('app.tenant_id') on every
-- connection checkout from the pool:
--   await client.query("SET app.tenant_id = $1", [tenantId]);
-- ============================================================================

-- Enable + force RLS on all tenant-scoped tables
ALTER TABLE users                       ENABLE ROW LEVEL SECURITY;
ALTER TABLE users                       FORCE ROW LEVEL SECURITY;
ALTER TABLE halls                       ENABLE ROW LEVEL SECURITY;
ALTER TABLE halls                       FORCE ROW LEVEL SECURITY;
ALTER TABLE rack_types                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE rack_types                  FORCE ROW LEVEL SECURITY;
ALTER TABLE processes                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE processes                   FORCE ROW LEVEL SECURITY;
ALTER TABLE rack_type_processes         ENABLE ROW LEVEL SECURITY;
ALTER TABLE rack_type_processes         FORCE ROW LEVEL SECURITY;
ALTER TABLE process_statuses            ENABLE ROW LEVEL SECURITY;
ALTER TABLE process_statuses            FORCE ROW LEVEL SECURITY;
ALTER TABLE status_templates            ENABLE ROW LEVEL SECURITY;
ALTER TABLE status_templates            FORCE ROW LEVEL SECURITY;
ALTER TABLE status_template_items       ENABLE ROW LEVEL SECURITY;
ALTER TABLE status_template_items       FORCE ROW LEVEL SECURITY;
ALTER TABLE groups                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE groups                      FORCE ROW LEVEL SECURITY;
ALTER TABLE racks                       ENABLE ROW LEVEL SECURITY;
ALTER TABLE racks                       FORCE ROW LEVEL SECURITY;
ALTER TABLE group_racks                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE group_racks                 FORCE ROW LEVEL SECURITY;
ALTER TABLE rack_process_states         ENABLE ROW LEVEL SECURITY;
ALTER TABLE rack_process_states         FORCE ROW LEVEL SECURITY;
ALTER TABLE rack_process_state_history  ENABLE ROW LEVEL SECURITY;
ALTER TABLE rack_process_state_history  FORCE ROW LEVEL SECURITY;
ALTER TABLE object_locks                ENABLE ROW LEVEL SECURITY;
ALTER TABLE object_locks                FORCE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens              ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens              FORCE ROW LEVEL SECURITY;

-- Policies: tenant_id = current_setting('app.tenant_id')::uuid

CREATE POLICY tenant_isolation ON users
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON halls
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON rack_types
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON processes
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON rack_type_processes
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON process_statuses
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON status_templates
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON status_template_items
    USING (template_id IN (
        SELECT id FROM status_templates
        WHERE tenant_id = current_setting('app.tenant_id')::uuid
    ))
    WITH CHECK (template_id IN (
        SELECT id FROM status_templates
        WHERE tenant_id = current_setting('app.tenant_id')::uuid
    ));

CREATE POLICY tenant_isolation ON groups
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON racks
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON group_racks
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON rack_process_states
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON rack_process_state_history
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON object_locks
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON refresh_tokens
    USING (user_id IN (
        SELECT id FROM users
        WHERE tenant_id = current_setting('app.tenant_id')::uuid
    ))
    WITH CHECK (user_id IN (
        SELECT id FROM users
        WHERE tenant_id = current_setting('app.tenant_id')::uuid
    ));


-- ============================================================================
-- VIEWS
-- ============================================================================

-- Rack progress: done+approved / total * 100
CREATE OR REPLACE VIEW v_rack_progress AS
SELECT
    r.id              AS rack_id,
    r.hall_id,
    r.tenant_id,
    r.name            AS rack_name,
    r.rack_type_id,
    COUNT(rps.id)     AS total_processes,
    COUNT(rps.id) FILTER (
        WHERE ps.is_done = TRUE AND rps.approval_status = 'APPROVED'
    )                 AS done_processes,
    CASE
        WHEN COUNT(rps.id) = 0 THEN 0
        ELSE ROUND(
            100.0 * COUNT(rps.id) FILTER (
                WHERE ps.is_done = TRUE AND rps.approval_status = 'APPROVED'
            ) / COUNT(rps.id), 1
        )
    END               AS progress_pct
FROM racks r
LEFT JOIN rack_process_states rps ON rps.rack_id = r.id
LEFT JOIN process_statuses ps ON ps.id = rps.status_id
GROUP BY r.id, r.hall_id, r.tenant_id, r.name, r.rack_type_id;

COMMENT ON VIEW v_rack_progress IS 'Per-rack progress: (done + approved) / total processes * 100.';

-- Hall progress: aggregated from rack progress
CREATE OR REPLACE VIEW v_hall_progress AS
SELECT
    hall_id,
    tenant_id,
    COUNT(DISTINCT rack_id) AS total_racks,
    SUM(total_processes)    AS total_processes,
    SUM(done_processes)     AS done_processes,
    CASE
        WHEN SUM(total_processes) = 0 THEN 0
        ELSE ROUND(100.0 * SUM(done_processes) / SUM(total_processes), 1)
    END                     AS progress_pct
FROM v_rack_progress
GROUP BY hall_id, tenant_id;

COMMENT ON VIEW v_hall_progress IS 'Aggregated progress per hall across all racks.';

-- Pending approvals queue for MANAGERs
CREATE OR REPLACE VIEW v_pending_approvals AS
SELECT
    rps.id              AS rack_process_state_id,
    rps.tenant_id,
    rps.rack_id,
    r.name              AS rack_name,
    r.hall_id,
    h.name              AS hall_name,
    rps.process_id,
    p.name              AS process_name,
    ps.name             AS requested_status,
    ps.color            AS status_color,
    rps.note,
    rps.updated_by,
    u.display_name      AS requested_by_name,
    rps.updated_at      AS requested_at
FROM rack_process_states rps
JOIN racks r ON r.id = rps.rack_id
JOIN halls h ON h.id = r.hall_id
JOIN processes p ON p.id = rps.process_id
JOIN process_statuses ps ON ps.id = rps.status_id
LEFT JOIN users u ON u.id = rps.updated_by
WHERE rps.approval_status = 'PENDING';

COMMENT ON VIEW v_pending_approvals IS 'Status changes waiting for MANAGER approval. Primary dashboard for MANAGERs.';

-- Active (non-expired) editing locks
CREATE OR REPLACE VIEW v_active_locks AS
SELECT
    ol.id,
    ol.tenant_id,
    ol.hall_id,
    ol.object_type,
    ol.object_id,
    ol.locked_by,
    u.display_name AS locked_by_name,
    ol.expires_at,
    ol.created_at
FROM object_locks ol
JOIN users u ON u.id = ol.locked_by
WHERE ol.expires_at > now();

COMMENT ON VIEW v_active_locks IS 'Currently active editing locks for realtime collaboration UI.';

-- Rack placements: standalone + group-based with absolute position
CREATE OR REPLACE VIEW v_rack_placements AS
SELECT
    r.id              AS rack_id,
    r.hall_id,
    r.tenant_id,
    r.name            AS rack_name,
    r.rack_type_id,
    rt.name           AS rack_type_name,
    r.pos_col         AS standalone_col,
    r.pos_row         AS standalone_row,
    gr.group_id,
    g.name            AS group_name,
    gr.local_col,
    gr.local_row,
    COALESCE(g.pos_col + gr.local_col, r.pos_col) AS abs_col,
    COALESCE(g.pos_row + gr.local_row, r.pos_row) AS abs_row
FROM racks r
LEFT JOIN rack_types rt ON rt.id = r.rack_type_id
LEFT JOIN group_racks gr ON gr.rack_id = r.id
LEFT JOIN groups g ON g.id = gr.group_id;

COMMENT ON VIEW v_rack_placements IS 'All rack placements with computed absolute grid coordinates. Standalone racks use pos_col/pos_row; grouped racks derive from group position + local offset.';


-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at on row modification
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY[
            'tenants', 'users', 'halls', 'status_templates',
            'processes', 'process_statuses', 'rack_types',
            'groups', 'racks', 'rack_process_states'
        ])
    LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()',
            tbl, tbl
        );
    END LOOP;
END;
$$;

-- Cleanup expired locks (call via pg_cron or application scheduler)
CREATE OR REPLACE FUNCTION fn_cleanup_expired_locks()
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM object_locks WHERE expires_at < now();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_cleanup_expired_locks IS 'Deletes expired object locks. Call periodically via pg_cron or application scheduler.';
