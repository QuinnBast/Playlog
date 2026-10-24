-- Playtime Steam counted but no agent recorded, rebuilt by catchup.recompute() (see catchup.py).
CREATE TABLE untracked_playtime (
    account_id bigint NOT NULL,
    app_id integer NOT NULL,
    period_start timestamptz NOT NULL,   -- happened somewhere between these two lifetime-total reports
    period_end timestamptz NOT NULL,
    minutes integer NOT NULL CHECK (minutes > 0),
    PRIMARY KEY (account_id, app_id, period_end)
);

-- All playtime for the dashboards: tracked sessions plus untracked time as device 'other', placed at the end of its
-- period (when it was noticed).
CREATE VIEW v_playtime AS
SELECT device, account_id, account, app_id, game, started_at AS at, seconds, true AS tracked
FROM v_sessions
UNION ALL
SELECT 'other', u.account_id, COALESCE(a.display_name, u.account_id::text), u.app_id,
       COALESCE(g.name, 'App ' || u.app_id), u.period_end, u.minutes * 60, false
FROM untracked_playtime u
LEFT JOIN accounts a USING (account_id)
LEFT JOIN games g USING (app_id);
