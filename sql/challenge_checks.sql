-- Champions Challenge: core numbers rebuilt in plain SQL (SQLite)
-- Used to cross-check the Python output. Tables are loaded raw by
-- scripts/run_sql_checks.py: activity, lessons, users.

-- 1. activity with learned minutes + week index (0 = 12-Sep-2022)
DROP VIEW IF EXISTS act;
CREATE VIEW act AS
SELECT a.user_id,
       a.lesson_id,
       a.activity_ts,
       date(a.activity_ts)                                         AS act_date,
       CAST(floor((julianday(date(a.activity_ts)) - julianday('2022-09-12')) / 7.0) AS INT) AS week_idx,
       a.day_completion_percentage / 100.0 * l.lesson_duration_in_mins AS learn_mins,
       MIN(a.overall_completion_percentage, 100)                   AS overall_pct
FROM activity a
JOIN lessons l ON l.lesson_id = a.lesson_id;

-- 2. bulk lessons: completed by >= 90% of users on the same day
DROP TABLE IF EXISTS bulk_lessons;
CREATE TABLE bulk_lessons AS
SELECT DISTINCT lesson_id
FROM (SELECT lesson_id, act_date, COUNT(DISTINCT user_id) AS n
      FROM act GROUP BY lesson_id, act_date)
WHERE n >= 0.9 * (SELECT COUNT(*) FROM users);

-- 3. users with an impossible day (> 24 hrs), after removing bulk rows
DROP TABLE IF EXISTS outlier_users;
CREATE TABLE outlier_users AS
SELECT DISTINCT user_id
FROM (SELECT user_id, act_date, SUM(learn_mins) / 60.0 AS hrs
      FROM act
      WHERE lesson_id NOT IN (SELECT lesson_id FROM bulk_lessons)
      GROUP BY user_id, act_date)
WHERE hrs > 24;

DROP VIEW IF EXISTS clean_act;
CREATE VIEW clean_act AS
SELECT *
FROM act
WHERE lesson_id NOT IN (SELECT lesson_id FROM bulk_lessons)
  AND user_id NOT IN (SELECT user_id FROM outlier_users);

-- 4. first completion of each lesson per user
DROP VIEW IF EXISTS completions;
CREATE VIEW completions AS
SELECT user_id, lesson_id, week_idx
FROM (SELECT user_id, lesson_id, week_idx,
             ROW_NUMBER() OVER (PARTITION BY user_id, lesson_id ORDER BY activity_ts) AS rn
      FROM clean_act
      WHERE overall_pct >= 100)
WHERE rn = 1;

-- 5. weekly goals during the challenge
DROP TABLE IF EXISTS user_week;
CREATE TABLE user_week AS
SELECT h.user_id, h.week_idx, h.hrs, COALESCE(c.n_lessons, 0) AS n_lessons,
       CASE WHEN h.hrs >= 3.5 AND COALESCE(c.n_lessons, 0) >= 5 THEN 1 ELSE 0 END AS goal_met
FROM (SELECT user_id, week_idx, SUM(learn_mins) / 60.0 AS hrs
      FROM clean_act WHERE week_idx BETWEEN 0 AND 7
      GROUP BY user_id, week_idx) h
LEFT JOIN (SELECT user_id, week_idx, COUNT(*) AS n_lessons
           FROM completions GROUP BY user_id, week_idx) c
  ON c.user_id = h.user_id AND c.week_idx = h.week_idx;

DROP TABLE IF EXISTS user_result;
CREATE TABLE user_result AS
SELECT u.user_id,
       COALESCE(SUM(w.goal_met), 0) AS goals,
       COALESCE(SUM(w.hrs), 0)      AS hrs,
       CASE WHEN COALESCE(SUM(w.goal_met), 0) >= 5 AND COALESCE(SUM(w.hrs), 0) >= 20 THEN 1 ELSE 0 END AS achieved
FROM users u
LEFT JOIN user_week w ON w.user_id = u.user_id
WHERE u.user_id NOT IN (SELECT user_id FROM outlier_users)
GROUP BY u.user_id;

-- checks ---------------------------------------------------------------

-- @check bulk_lessons
SELECT COUNT(*) AS n FROM bulk_lessons;

-- @check outlier_users
SELECT group_concat(user_id, ',') AS ids FROM (SELECT user_id FROM outlier_users ORDER BY user_id);

-- @check achievers
SELECT SUM(achieved) AS n FROM user_result;

-- @check goodies_by_goals
SELECT goals, COUNT(*) AS n FROM user_result WHERE achieved = 0 GROUP BY goals ORDER BY goals;

-- @check top5
SELECT user_id, ROUND(hrs, 2) AS hrs, goals FROM user_result
WHERE achieved = 1 ORDER BY hrs DESC LIMIT 5;

-- @check avg_weekly_hours_by_period
SELECT CASE WHEN week_idx >= 0 THEN 'During' ELSE 'Before' END AS period,
       ROUND(SUM(learn_mins) / 60.0 / 8 / (SELECT COUNT(*) FROM user_result), 3) AS hrs_per_user_week
FROM clean_act GROUP BY 1;
