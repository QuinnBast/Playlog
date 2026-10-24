-- The local time zone for days, weekdays and hours (heatmaps, averages, streaks, "per day" charts), set by the
-- server from its TZ setting at every start. One row.
CREATE TABLE settings (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    timezone text NOT NULL DEFAULT 'UTC'
);
INSERT INTO settings DEFAULT VALUES;

CREATE OR REPLACE VIEW v_session_hours AS
SELECT s.uuid,
       s.device,
       s.account_id,
       s.account,
       s.app_id,
       s.game,
       h.local_hour AT TIME ZONE z.timezone AS hour,   -- start of the hour (timestamptz, for time filters)
       h.local_hour,                                   -- the same, as local wall-clock time
       EXTRACT(EPOCH FROM LEAST(l.local_end, h.local_hour + interval '1 hour')
                        - GREATEST(l.local_start, h.local_hour))::integer AS seconds
FROM v_sessions s
CROSS JOIN settings z
CROSS JOIN LATERAL (SELECT s.started_at AT TIME ZONE z.timezone AS local_start,
                           s.ended_at AT TIME ZONE z.timezone AS local_end) l
CROSS JOIN LATERAL generate_series(date_trunc('hour', l.local_start), l.local_end, interval '1 hour') AS h (local_hour)
WHERE l.local_end > h.local_hour OR l.local_start = l.local_end;
