-- Times are stored as timestamptz (UTC); days and hours on the dashboards use the server's TZ setting.

CREATE TABLE devices (
    name text PRIMARY KEY,
    last_seen_at timestamptz,
    -- The game being played right now, as of last_seen_at (the agent reports it every minute while reachable).
    current_account_id bigint,
    current_app_id integer,
    current_started_at timestamptz
);

-- Display names come from the server's ACCOUNTS setting, not from Steam (both profiles are private).
CREATE TABLE accounts (
    account_id bigint PRIMARY KEY,   -- 32-bit Steam account ID; SteamID64 = 76561197960265728 + this
    display_name text NOT NULL
);

CREATE TABLE games (
    app_id integer PRIMARY KEY,
    name text,                       -- NULL: the store doesn't know it (delisted, tool, ...); retried later
    source text NOT NULL,            -- 'manifest' (from a device's installed games) or 'store'
    fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    uuid uuid PRIMARY KEY,           -- made by the agent, so re-uploads are ignored
    device text NOT NULL REFERENCES devices (name),
    account_id bigint NOT NULL,
    app_id integer NOT NULL,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ended_at >= started_at)
);
CREATE INDEX sessions_started_at ON sessions (started_at);
CREATE INDEX sessions_account_app ON sessions (account_id, app_id, started_at);

-- Lifetime playtime as each device's Steam reported it. A row is only added when the value changed since that
-- device's previous report, so this is a change log rather than a daily copy of every game.
CREATE TABLE totals_snapshots (
    id bigserial PRIMARY KEY,
    device text NOT NULL REFERENCES devices (name),
    account_id bigint NOT NULL,
    app_id integer NOT NULL,
    playtime_minutes integer NOT NULL,
    last_played timestamptz,
    taken_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX totals_snapshots_latest ON totals_snapshots (device, account_id, app_id, taken_at DESC);

-- What the dashboards query.
CREATE VIEW v_sessions AS
SELECT s.uuid,
       s.device,
       s.account_id,
       COALESCE(a.display_name, s.account_id::text) AS account,
       s.app_id,
       COALESCE(g.name, 'App ' || s.app_id) AS game,
       s.started_at,
       s.ended_at,
       EXTRACT(EPOCH FROM s.ended_at - s.started_at)::integer AS seconds
FROM sessions s
LEFT JOIN accounts a USING (account_id)
LEFT JOIN games g USING (app_id);

-- Latest lifetime total per account and game. Steam syncs these across devices, so take the newest report from any
-- device - never add two devices' numbers together.
CREATE VIEW v_lifetime_totals AS
SELECT DISTINCT ON (t.account_id, t.app_id)
       t.account_id,
       COALESCE(a.display_name, t.account_id::text) AS account,
       t.app_id,
       COALESCE(g.name, 'App ' || t.app_id) AS game,
       t.playtime_minutes,
       t.last_played,
       t.taken_at,
       t.device
FROM totals_snapshots t
LEFT JOIN accounts a USING (account_id)
LEFT JOIN games g USING (app_id)
ORDER BY t.account_id, t.app_id, t.playtime_minutes DESC, t.taken_at DESC;
