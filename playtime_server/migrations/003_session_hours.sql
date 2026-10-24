-- Tracked sessions split into the local clock hours they cover, for the day / weekday / hour-of-day
-- heatmaps: a session from 21:40 to 23:10 counts 20 min at 21:00, 60 at 22:00 and 10 at 23:00, and one that crosses
-- midnight counts on both days. Untracked time isn't here (its time of day is unknown).
-- 004_timezone.sql replaces this view with one in the configured time zone.
CREATE VIEW v_session_hours AS
SELECT s.uuid,
       s.device,
       s.account_id,
       s.account,
       s.app_id,
       s.game,
       h.local_hour AT TIME ZONE 'UTC' AS hour,   -- start of the hour (timestamptz, for time filters)
       h.local_hour,                                         -- the same, as local wall-clock time
       EXTRACT(EPOCH FROM LEAST(l.local_end, h.local_hour + interval '1 hour')
                        - GREATEST(l.local_start, h.local_hour))::integer AS seconds
FROM v_sessions s
CROSS JOIN LATERAL (SELECT s.started_at AT TIME ZONE 'UTC' AS local_start,
                           s.ended_at AT TIME ZONE 'UTC' AS local_end) l
CROSS JOIN LATERAL generate_series(date_trunc('hour', l.local_start), l.local_end, interval '1 hour') AS h (local_hour)
WHERE l.local_end > h.local_hour OR l.local_start = l.local_end;
