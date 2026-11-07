-- Spec 03 §3: PPC station network, state model, commands, settings and live_status.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS locations (
    location_id text PRIMARY KEY,
    name text NOT NULL,
    operator text NOT NULL DEFAULT 'PPC Blue',
    partner text,
    address text,
    county_code text NOT NULL,
    geom geography(Point, 4326) NOT NULL,
    context_type text NOT NULL CHECK (context_type IN ('urban', 'retail', 'highway', 'airport', 'other')),
    primary_source text NOT NULL,
    confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    cp_count_estimated boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_locations_geom ON locations USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_locations_county ON locations (county_code);

CREATE TABLE IF NOT EXISTS charge_points (
    cp_id text PRIMARY KEY,
    location_id text NOT NULL REFERENCES locations(location_id) ON DELETE CASCADE,
    power_class text NOT NULL CHECK (power_class IN ('AC_22', 'DC_50', 'DC_HPC')),
    max_kw numeric,
    connectors text[] NOT NULL DEFAULT '{}',
    current_type text NOT NULL CHECK (current_type IN ('AC', 'DC'))
);

CREATE INDEX IF NOT EXISTS idx_charge_points_location ON charge_points (location_id);

CREATE TABLE IF NOT EXISTS source_refs (
    source text NOT NULL,
    source_id text NOT NULL,
    location_id text NOT NULL REFERENCES locations(location_id) ON DELETE CASCADE,
    cp_id text REFERENCES charge_points(cp_id) ON DELETE SET NULL,
    PRIMARY KEY (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_source_refs_location ON source_refs (location_id);

CREATE TABLE IF NOT EXISTS cp_state (
    cp_id text PRIMARY KEY REFERENCES charge_points(cp_id) ON DELETE CASCADE,
    state text NOT NULL CHECK (state IN ('CHARGING', 'IDLE', 'WAKING', 'STANDBY', 'DEEP_SLEEP', 'OFFLINE')),
    since_ts timestamptz NOT NULL DEFAULT now(),
    last_command_id bigint
);

CREATE TABLE IF NOT EXISTS state_events (
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL DEFAULT now(),
    cp_id text NOT NULL,
    from_state text NOT NULL CHECK (from_state IN ('CHARGING', 'IDLE', 'WAKING', 'STANDBY', 'DEEP_SLEEP', 'OFFLINE')),
    to_state text NOT NULL CHECK (to_state IN ('CHARGING', 'IDLE', 'WAKING', 'STANDBY', 'DEEP_SLEEP', 'OFFLINE')),
    cause text NOT NULL CHECK (cause IN ('agent', 'operator', 'arrival', 'departure', 'fault', 'live_status')),
    run_id uuid
);

CREATE INDEX IF NOT EXISTS idx_state_events_cp_ts ON state_events (cp_id, ts);
CREATE INDEX IF NOT EXISTS idx_state_events_run_ts ON state_events (run_id, ts);

CREATE TABLE IF NOT EXISTS commands (
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL DEFAULT now(),
    run_id uuid,
    cp_id text NOT NULL,
    policy text NOT NULL,
    from_state text NOT NULL CHECK (from_state IN ('CHARGING', 'IDLE', 'WAKING', 'STANDBY', 'DEEP_SLEEP', 'OFFLINE')),
    target_state text NOT NULL CHECK (target_state IN ('IDLE', 'STANDBY', 'DEEP_SLEEP')),
    reason text NOT NULL DEFAULT '',
    status text NOT NULL CHECK (status IN ('applied', 'pending', 'approved', 'rejected', 'expired')),
    expires_at timestamptz,
    decided_by text,
    decided_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_commands_run_status ON commands (run_id, status);
CREATE INDEX IF NOT EXISTS idx_commands_cp_ts ON commands (cp_id, ts);

CREATE TABLE IF NOT EXISTS settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL
);

INSERT INTO settings (key, value)
VALUES ('autonomy_mode', '"ACT"'::jsonb)
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS live_status (
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL,
    cp_id text,
    source text NOT NULL,
    source_location_id text,
    source_cp_id text,
    connector text,
    raw_status text NOT NULL,
    status text NOT NULL CHECK (status IN ('CHARGING', 'AVAILABLE', 'OFFLINE', 'UNKNOWN'))
);

CREATE INDEX IF NOT EXISTS idx_live_status_cp_ts ON live_status (cp_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_live_status_ts ON live_status (ts DESC);
