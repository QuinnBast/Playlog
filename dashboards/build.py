"""Generates steam-playtime.json and steam-sessions.json (Grafana, over the playtime Postgres).

    python dashboards/build.py dashboards

Edit this, not the JSON (which it overwrites). The dev stack picks up changes after: docker compose -f dev/docker-compose.yml restart grafana
"""
import json
import sys
from pathlib import Path

DS = {"type": "grafana-postgresql-datasource", "uid": "steam-playtime"}
# The local time zone, as the server set it from its TZ setting (see migrations/004_timezone.sql).
TZ = "(SELECT timezone FROM settings)"
ACC = "account IN ($account)"

# Validated (dark surface): accounts blue/orange; devices aqua/violet/yellow.
PRIMARY_COLOR, OTHER_ACCOUNT = "#3987e5", "#d95926"   # PRIMARY_ACCOUNT, and everyone else
# Display name (from the server's ACCOUNTS) that gets the first account color.
PRIMARY_ACCOUNT = "Quinn"
DEVICE_COLORS = {"pc": "#199e70", "deck": "#9085e9", "other": "#c98500"}

panels = []
_id = [0]


def panel(kind, title, grid, sql, fmt="table", description=None, **extra):
    _id[0] += 1
    p = {
        "id": _id[0], "type": kind, "title": title, "datasource": DS,
        "gridPos": dict(zip("xywh", grid)),
        "targets": [{"refId": "A", "datasource": DS, "editorMode": "code", "format": fmt, "rawQuery": True,
                     "rawSql": " ".join(sql.split())}],
    }
    if description:
        p["description"] = description
    p.update(extra)
    panels.append(p)
    return p


def by_name(name, color):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


def account_colors():
    # Everyone but the primary account gets the second account color.
    return {"defaults": {"color": {"mode": "fixed", "fixedColor": OTHER_ACCOUNT}, "unit": "suffix: h",
                         "decimals": 1, "min": 0}, "overrides": [by_name(PRIMARY_ACCOUNT, PRIMARY_COLOR)]}


def device_colors():
    return {"defaults": {"color": {"mode": "fixed", "fixedColor": DEVICE_COLORS["other"]}, "unit": "suffix: h",
                         "decimals": 1, "min": 0},
            "overrides": [by_name(k, v) for k, v in DEVICE_COLORS.items()]}


def stat(title, grid, sql, unit="none", decimals=0, description=None, text=False):
    text_size = {"text": {"valueSize": 26}} if text else {}
    return panel("stat", title, grid, sql, description=description,
                 fieldConfig={"defaults": {"unit": unit, "decimals": decimals,
                                           "color": {"mode": "fixed", "fixedColor": "text"}}, "overrides": []},
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/" if text else "",
                                            "values": False},
                          "colorMode": "none", "graphMode": "none", "textMode": "value", "justifyMode": "auto",
                          "orientation": "auto", "wideLayout": True, "showPercentChange": False, **text_size})


STREAK_CTE = f"""
WITH days AS (SELECT DISTINCT (at AT TIME ZONE {TZ})::date AS d FROM v_playtime WHERE tracked AND {ACC}),
     g AS (SELECT d, d - (row_number() OVER (ORDER BY d))::int AS grp FROM days)
"""

# --- Row: headline numbers --------------------------------------------------------------------------------------
stat("Hours played", (0, 0, 4, 4),
     f"SELECT COALESCE(round(sum(seconds) / 3600.0, 1), 0) AS hours FROM v_playtime WHERE $__timeFilter(at) AND {ACC}",
     unit="suffix: h", decimals=1,
     description="Tracked sessions plus untracked time (\"other\": Steam counted it, no agent recorded it).")
stat("Sessions", (4, 0, 3, 4),
     f"SELECT count(*) FROM v_sessions WHERE $__timeFilter(started_at) AND {ACC}")
stat("Longest session", (7, 0, 4, 4),
     f"SELECT COALESCE(max(seconds), 0) / 3600.0 FROM v_sessions WHERE $__timeFilter(started_at) AND {ACC}",
     unit="suffix: h", decimals=1)
stat("Current streak", (11, 0, 4, 4),
     STREAK_CTE + f"""SELECT CASE WHEN max(d) >= (now() AT TIME ZONE {TZ})::date - 1 THEN count(*) ELSE 0 END
     FROM g WHERE grp = (SELECT grp FROM g ORDER BY d DESC LIMIT 1)""",
     unit="suffix:  days", description="Consecutive days with a tracked session, up to today or yesterday.")
stat("Longest streak", (15, 0, 4, 4),
     STREAK_CTE + "SELECT COALESCE(max(c), 0) FROM (SELECT count(*) AS c FROM g GROUP BY grp) s", unit="suffix:  days",
     description="All time, not just the selected range.")
stat("Now playing", (19, 0, 5, 4),
     """SELECT COALESCE(string_agg(COALESCE(a.display_name, d.current_account_id::text) || ': ' ||
            COALESCE(g.name, 'App ' || d.current_app_id) || ' (' || d.name || ')', ' · '), 'Nothing') AS now
     FROM devices d
     LEFT JOIN accounts a ON a.account_id = d.current_account_id
     LEFT JOIN games g ON g.app_id = d.current_app_id
     WHERE d.current_app_id IS NOT NULL AND d.last_seen_at > now() - interval '3 minutes'""",
     text=True, description="Live from the agents (updated every minute while the device can reach the server).")


# --- Row: when (heatmaps over v_session_hours: tracked sessions split into local clock hours) -----------------
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Like GitHub's dark mode: no play is blank, more play is a brighter green.
HOURS_GREEN = {"mode": "opacity", "fill": "#39d353", "scale": "exponential", "exponent": 0.6, "min": 0,
               "steps": 64}
day_columns = ", ".join(
    f"""round(COALESCE(sum(s.seconds) FILTER (WHERE EXTRACT(ISODOW FROM s.local_hour) = {i + 1}), 0) / 3600.0, 2)
        AS "{d}\"""" for i, d in reversed(list(enumerate(WEEKDAYS))))
panel("heatmap", "Days played", (0, 53, 24, 7),
      f"""SELECT w.week AT TIME ZONE {TZ} AS time, {day_columns}
          FROM generate_series(date_trunc('week', $__timeFrom() AT TIME ZONE {TZ}),
                               $__timeTo() AT TIME ZONE {TZ}, interval '1 week') AS w (week)
          LEFT JOIN v_session_hours s
                 ON date_trunc('week', s.local_hour) = w.week AND $__timeFilter(s.hour) AND s.account IN ($account)
          GROUP BY w.week ORDER BY w.week""",
      description="Hours of tracked play per day over the last year, one column per week (like GitHub's "
                  "contribution graph). Not limited by the time range.",
      timeFrom="1y",
      fieldConfig={"defaults": {"unit": "suffix: h", "decimals": 1}, "overrides": []},
      options={"calculate": False, "cellGap": 2, "cellValues": {"unit": "suffix: h", "decimals": 1},
               "color": HOURS_GREEN, "yAxis": {"axisPlacement": "left", "reverse": False},
               "rowsFrame": {"layout": "unknown"}, "showValue": "never", "legend": {"show": True},
               "tooltip": {"mode": "single", "yHistogram": False, "showColorScale": False},
               "exemplars": {"color": "transparent"}, "filterValues": {"le": 0.01}})

hour_columns = ", ".join(
    f"""round(COALESCE(sum(s.seconds) FILTER (WHERE EXTRACT(HOUR FROM s.local_hour) = {h}), 0) / 3600.0, 1)
        AS "{h:02d}\"""" for h in range(24))
weekday_values = ", ".join(f"({i + 1}, '{d}')" for i, d in enumerate(WEEKDAYS))
panel("table", "Total hours by weekday × hour of day", (0, 60, 24, 10),
      f"""SELECT d.label AS "Day", {hour_columns}
          FROM (VALUES {weekday_values}) AS d (dow, label)
          LEFT JOIN v_session_hours s
                 ON EXTRACT(ISODOW FROM s.local_hour) = d.dow AND $__timeFilter(s.hour) AND s.account IN ($account)
          GROUP BY d.dow, d.label ORDER BY d.dow""",
      description="Each cell: total hours of tracked play in that clock hour (local time) on that weekday, added "
                  "up over the whole selected range - e.g. Thu / 21 = all play between 21:00 and 22:00 on every "
                  "Thursday in the range. A session counts in every hour it covers.",
      fieldConfig={"defaults": {"unit": "none", "decimals": 1, "min": 0,
                                "color": {"mode": "continuous-greens"},
                                "custom": {"align": "center", "minWidth": 40, "cellOptions": {"type": "color-background",
                                                                              "mode": "gradient"}},
                                "mappings": [{"type": "value", "options": {"0": {"text": " "}}}]},
                   "overrides": [{"matcher": {"id": "byName", "options": "Day"},
                                  "properties": [{"id": "custom.width", "value": 70},
                                                 {"id": "custom.cellOptions", "value": {"type": "auto"}},
                                                 {"id": "custom.align", "value": "left"}]}]},
      options={"showHeader": True, "cellHeight": "md", "footer": {"show": False}})


# --- Row: over time ---------------------------------------------------------------------------------------------
def over_time(title, grid, series, colors):
    return panel(
        "timeseries", title, grid,
        f"""SELECT date_trunc('$bucket', at AT TIME ZONE {TZ}) AT TIME ZONE {TZ} AS time, {series} AS metric,
               round(sum(seconds) / 3600.0, 2) AS value
            FROM v_playtime WHERE $__timeFilter(at) AND {ACC} GROUP BY 1, 2 ORDER BY 1""",
        fmt="time_series",
        fieldConfig={**colors, "defaults": {**colors["defaults"], "custom": {
            "drawStyle": "bars", "barAlignment": -1, "barWidthFactor": 0.8, "fillOpacity": 85, "lineWidth": 1,
            "stacking": {"mode": "normal", "group": "A"}, "showPoints": "never", "axisSoftMin": 0,
            "gradientMode": "none"}}},
        options={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                 "tooltip": {"mode": "multi", "sort": "desc"}})


over_time("Hours per $bucket by account", (0, 4, 12, 9), "account", account_colors())
over_time("Hours per $bucket by device", (12, 4, 12, 9), "device", device_colors())

# --- Row: averages (tracked sessions, per calendar day in the range, including days without play) -------------
# Every (bucket, account) pair gets a row, so buckets nobody played in still show and each account keeps its column.
AVG_DAYS = f"""
days AS (SELECT d::date AS day FROM generate_series(($__timeFrom() AT TIME ZONE {TZ})::date,
                                                    ($__timeTo() AT TIME ZONE {TZ})::date, interval '1 day') d),
accounts AS (SELECT DISTINCT account FROM v_sessions WHERE {ACC} AND $__timeFilter(started_at)),
played AS (SELECT * FROM v_session_hours WHERE $__timeFilter(hour) AND {ACC})"""


def average_bars(title, grid, sql, label, unit, decimals, description):
    colors = account_colors()
    return panel(
        "barchart", title, grid, sql, description=description,
        transformations=[
            {"id": "groupingToMatrix", "options": {"columnField": "account", "rowField": "bucket",
                                                   "valueField": "value", "emptyValue": "zero"}},
            {"id": "organize", "options": {"renameByName": {"bucket\\account": label}}},
        ],
        fieldConfig={"defaults": {**colors["defaults"], "unit": unit, "decimals": decimals,
                                  "custom": {"fillOpacity": 85, "lineWidth": 0}},
                     "overrides": colors["overrides"] + [{"matcher": {"id": "byName", "options": label},
                                                          "properties": [{"id": "unit", "value": "string"}]}]},
        options={"orientation": "vertical", "stacking": "normal", "xField": label, "showValue": "never",
                 "barWidth": 0.8, "groupWidth": 0.7,
                 "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                 "tooltip": {"mode": "multi", "sort": "none"}})


average_bars(
    "Average hours played per weekday", (0, 13, 12, 8),
    f"""WITH {AVG_DAYS},
         weekdays AS (SELECT EXTRACT(ISODOW FROM day)::int AS dow, count(*) AS n FROM days GROUP BY 1)
        SELECT w.label AS bucket, a.account, COALESCE(sum(p.seconds), 0) / 3600.0 / n.n AS value
        FROM (VALUES {weekday_values}) AS w (dow, label)
        JOIN weekdays n ON n.dow = w.dow
        CROSS JOIN accounts a
        LEFT JOIN played p ON EXTRACT(ISODOW FROM p.local_hour) = w.dow AND p.account = a.account
        GROUP BY w.dow, w.label, a.account, n.n ORDER BY w.dow, a.account""",
    "Day", "suffix: h", 1,
    "Tracked hours on each weekday, averaged over every such day in the range (days without play count as 0).")

hour_values = ", ".join(f"({h}, '{h:02d}')" for h in range(24))
average_bars(
    "Average time played per hour of day", (12, 13, 12, 8),
    f"""WITH {AVG_DAYS}
        SELECT h.label AS bucket, a.account, COALESCE(sum(p.seconds), 0) / 60.0 / (SELECT count(*) FROM days) AS value
        FROM (VALUES {hour_values}) AS h (hour, label)
        CROSS JOIN accounts a
        LEFT JOIN played p ON EXTRACT(HOUR FROM p.local_hour) = h.hour AND p.account = a.account
        GROUP BY h.hour, h.label, a.account ORDER BY h.hour, a.account""",
    "Hour", "m", 0,
    "Minutes of tracked play in each clock hour (local time) on an average day in the range - e.g. 21 = 21:00 "
    "to 22:00. Days without play count as 0.")


# --- Row: games and splits --------------------------------------------------------------------------------------
panel("barchart", "Top games", (0, 21, 12, 12),
      f"""WITH top AS (SELECT app_id FROM v_playtime WHERE $__timeFilter(at) AND {ACC}
                       GROUP BY app_id ORDER BY sum(seconds) DESC LIMIT 15)
          SELECT p.game, p.account, round(sum(p.seconds) / 3600.0, 1) AS hours
          FROM v_playtime p JOIN top USING (app_id) WHERE $__timeFilter(p.at) AND p.account IN ($account)
          GROUP BY 1, 2""",
      transformations=[
          {"id": "groupingToMatrix", "options": {"columnField": "account", "rowField": "game", "valueField": "hours",
                                                 "emptyValue": "zero"}},
          {"id": "calculateField", "options": {"mode": "reduceRow", "reduce": {"reducer": "sum"}, "alias": "Total"}},
          {"id": "sortBy", "options": {"sort": [{"field": "Total", "desc": True}]}},
          {"id": "organize", "options": {"excludeByName": {"Total": True}, "renameByName": {"game\\account": "Game"}}},
      ],
      fieldConfig={**account_colors(), "defaults": {**account_colors()["defaults"],
                                                     "custom": {"fillOpacity": 85, "lineWidth": 0}}},
      options={"orientation": "horizontal", "stacking": "normal", "xField": "Game", "showValue": "never",
               "barWidth": 0.8, "groupWidth": 0.7, "xTickLabelMaxLength": 32,
               "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
               "tooltip": {"mode": "multi", "sort": "desc"}})


def split(title, grid, series, colors):
    return panel(
        "bargauge", title, grid,
        f"""SELECT now() AS time, {series} AS metric, round(sum(seconds) / 3600.0, 1) AS value
            FROM v_playtime WHERE $__timeFilter(at) AND {ACC} GROUP BY 2 ORDER BY 3 DESC""",
        fmt="time_series", fieldConfig=colors,
        options={"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "namePlacement": "top",
                 "valueMode": "text", "minVizHeight": 16, "minVizWidth": 8, "sizing": "auto",
                 "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}})


split("Hours by account", (12, 21, 6, 6), "account", account_colors())
split("Hours by device", (18, 21, 6, 6), "device", device_colors()).update(
    description="\"other\": playtime Steam counted that no agent recorded (another device, or before tracking).")

panel("table", "Devices", (12, 27, 12, 6),
      """SELECT d.name AS "Device", d.last_seen_at AS "Last contact",
                (SELECT max(ended_at) FROM sessions s WHERE s.device = d.name) AS "Latest session",
                (SELECT count(*) FROM sessions s WHERE s.device = d.name) AS "Sessions"
         FROM devices d ORDER BY d.name""",
      description="When each agent last reached the server. The Deck queues sessions while away and uploads them "
                  "when it's back on the home network.",
      fieldConfig={"defaults": {"unit": "none"}, "overrides": [
          {"matcher": {"id": "byType", "options": "time"}, "properties": [{"id": "unit", "value": "dateTimeFromNow"}]}]},
      options={"showHeader": True, "cellHeight": "sm"})

# --- Row: sessions and lifetime ---------------------------------------------------------------------------------
SESSION_COLS = """started_at AS "Started", account AS "Account", device AS "Device", game AS "Game",
                  seconds / 3600.0 AS "Hours" FROM v_sessions"""
session_fields = {"defaults": {"unit": "none"}, "overrides": [
    {"matcher": {"id": "byName", "options": "Hours"},
     "properties": [{"id": "unit", "value": "suffix: h"}, {"id": "decimals", "value": 1}]},
    {"matcher": {"id": "byName", "options": "Started"},
     "properties": [{"id": "unit", "value": "time: YYYY-MM-DD HH:mm"}, {"id": "custom.width", "value": 135}]},
    *[{"matcher": {"id": "byName", "options": name}, "properties": [{"id": "custom.width", "value": width}]}
      for name, width in (("Account", 115), ("Device", 70), ("Hours", 70))]]}
panel("table", "Longest sessions", (0, 33, 12, 10),
      f"SELECT {SESSION_COLS} WHERE $__timeFilter(started_at) AND {ACC} ORDER BY seconds DESC LIMIT 25",
      fieldConfig=session_fields, options={"showHeader": True, "cellHeight": "sm"})
panel("table", "Recent sessions", (12, 33, 12, 10),
      f"SELECT {SESSION_COLS} WHERE $__timeFilter(started_at) AND {ACC} ORDER BY started_at DESC LIMIT 50",
      fieldConfig=session_fields, options={"showHeader": True, "cellHeight": "sm"})
panel("table", "Lifetime playtime (Steam's totals)", (0, 43, 24, 10),
      f"""SELECT account AS "Account", game AS "Game", playtime_minutes / 60.0 AS "Lifetime hours",
                 last_played AS "Last played"
          FROM v_lifetime_totals WHERE {ACC} ORDER BY playtime_minutes DESC""",
      description="Steam's own lifetime totals, including everything before tracking began. Not limited by the "
                  "time range.",
      fieldConfig={"defaults": {"unit": "none"}, "overrides": [
          {"matcher": {"id": "byName", "options": "Lifetime hours"},
           "properties": [{"id": "unit", "value": "suffix: h"}, {"id": "decimals", "value": 1},
                          {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic",
                                                                  "valueDisplayMode": "text"}},
                          {"id": "color", "value": {"mode": "fixed", "fixedColor": PRIMARY_COLOR}}]},
          {"matcher": {"id": "byName", "options": "Last played"},
           "properties": [{"id": "unit", "value": "time: YYYY-MM-DD"}]}]},
      options={"showHeader": True, "cellHeight": "sm"})

# Every known account: configured ones (ACCOUNTS), plus any with totals or sessions. Never empty once the server has
# started - an empty list would put "account IN ()" into every query, which is a syntax error.
ACCOUNTS_SQL = ("SELECT display_name FROM accounts UNION SELECT account FROM v_lifetime_totals "
                "UNION SELECT account FROM v_playtime ORDER BY 1")
ACCOUNT_VAR = {"name": "account", "label": "Account", "type": "query", "datasource": DS,
               "query": ACCOUNTS_SQL, "definition": ACCOUNTS_SQL,
               "multi": True, "includeAll": True, "current": {"text": "All", "value": "$__all"}, "refresh": 1,
               "sort": 1}


def link(title, uid):
    return {"title": title, "type": "link", "url": f"/d/{uid}", "icon": "dashboard", "keepTime": True,
            "includeVars": True, "targetBlank": False, "asDropdown": False, "tags": [], "tooltip": ""}


out = Path(sys.argv[1])
dashboard = {
    "uid": "steam-playtime",
    "title": "Steam playtime",
    "description": "Every recorded Steam session, every account and device (github.com/QuinnBast/Playlog).",
    "tags": ["steam", "games"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "1m",
    "schemaVersion": 39,
    "time": {"from": "now-30d", "to": "now"},
    "links": [link("All sessions (timeline)", "steam-sessions")],
    "templating": {"list": [
        ACCOUNT_VAR,
        {"name": "bucket", "label": "Group by", "type": "custom", "query": "day,week,month",
         "current": {"text": "day", "value": "day"},
         "options": [{"text": v, "value": v, "selected": v == "day"} for v in ("day", "week", "month")]},
    ]},
    "panels": panels,
}
json.dump(dashboard, open(out / "steam-playtime.json", "w", newline="\n"), indent=2)
print(f"steam-playtime: {len(panels)} panels")

# --- Sessions dashboard: every session in the range as a bar on a timeline ------------------------------------
panels = []
# One row per game + account + device. A row gets a value at each session's start (who and how long) and an empty
# value at its end; "partition by values" makes each row its own series, so rows don't cut each other's bars short.
ROW = "s.game || '  ·  ' || s.account || '  ·  ' || s.device"
panel("state-timeline", "Every session", (0, 0, 24, 32),
      f"""SELECT t.time, t.row, t.value FROM v_sessions s
          CROSS JOIN LATERAL (VALUES
              (s.started_at, {ROW}, s.account || '|' || to_char(s.seconds / 3600.0, 'FM990.0') || ' h'),
              (s.ended_at, {ROW}, NULL)) AS t (time, row, value)
          WHERE $__timeFilter(s.started_at) AND s.account IN ($account) AND s.seconds >= 60
          ORDER BY t.row, t.time""",
      description="Each bar is one session: its row says what was played, by whom and on which device; its length "
                  "is how long. Drag across the chart to zoom in.",
      transformations=[{"id": "partitionByValues",
                        "options": {"fields": ["row"], "keepFields": False, "naming": {"asLabels": True}}}],
      fieldConfig={
          "defaults": {
              "displayName": "${__field.labels.row}",
              "color": {"mode": "fixed", "fixedColor": OTHER_ACCOUNT},
              "custom": {"fillOpacity": 85, "lineWidth": 0, "spanNulls": False, "insertNulls": False},
              "mappings": [
                  {"type": "regex", "options": {"pattern": f"^{PRIMARY_ACCOUNT}\\|(.*)$",
                                                "result": {"text": "$1", "color": PRIMARY_COLOR, "index": 0}}},
                  {"type": "regex", "options": {"pattern": "^[^|]*\\|(.*)$",
                                                "result": {"text": "$1", "color": OTHER_ACCOUNT, "index": 1}}},
              ]},
          "overrides": []},
      options={"mergeValues": False, "showValue": "never", "alignValue": "center", "rowHeight": 0.8,
               "legend": {"showLegend": False}, "tooltip": {"mode": "single", "sort": "none"}})

sessions_dashboard = {
    "uid": "steam-sessions",
    "title": "Steam sessions",
    "description": "Every Steam session in the selected range on one timeline.",
    "tags": ["steam", "games"],
    "timezone": "browser",
    "editable": True,
    "refresh": "1m",
    "schemaVersion": 39,
    "time": {"from": "now-7d", "to": "now"},
    "links": [link("Steam playtime", "steam-playtime")],
    "templating": {"list": [ACCOUNT_VAR]},
    "panels": panels,
}
json.dump(sessions_dashboard, open(out / "steam-sessions.json", "w", newline="\n"), indent=2)
print(f"steam-sessions: {len(panels)} panels")
