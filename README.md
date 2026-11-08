# Champions Challenge – engagement & rewards analysis

Take-home for the Data Analyst role. The question: did the 8-week Champions
Challenge (12 Sep – 6 Nov 2022) improve learning engagement, how well did
users stick with it, and who gets which reward.

Dashboard: Looker Studio (link in the submission email)
Report: `Champions Challenge – Insights & Recommendations` (PDF)

## Layout

```
data/raw/          original files as received
data/processed/    tables the dashboard reads (CSV + one xlsx with all tabs)
scripts/
  prepare_data.py    cleaning, challenge rules, reward allocation, output tables
  run_sql_checks.py  rebuilds the headline numbers in SQLite as a cross-check
sql/
  challenge_checks.sql
docs/
  looker_studio_build_guide.md
```

## Running it

With [uv](https://docs.astral.sh/uv/) (creates the venv and installs deps on first run):

```
uv run scripts/prepare_data.py
uv run scripts/run_sql_checks.py
```

Or plain pip, Python 3.10+:

```
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

python scripts/prepare_data.py
python scripts/run_sql_checks.py
```

`prepare_data.py` overwrites everything in `data/processed/`.

## Rules as implemented

- **Learning time** per record = `day_completion_percentage / 100 × lesson_duration_in_mins`.
- **Lesson completed** on the day `overall_completion_percentage` first reaches 100.
  Re-completing the same lesson later (about 530 user–lesson pairs) adds learning
  time but doesn't count as another completed lesson.
- **Weeks** run Monday–Sunday. W1 starts 12 Sep 2022. The 8 weeks before
  (18 Jul – 11 Sep) form the baseline.
- **Weekly goal** = 5+ lessons completed and 3.5+ hrs learned in the week.
- **Target** = 5+ weekly goals and 20+ hrs over the 8 weeks.
- **Top 3 ranking**: challenge hours, then weekly goals, then lessons completed
  during the challenge, then the earlier date the target was reached.
  user_218 and user_309 tie on hours (153.53) and goals (8). user_218 gets 2nd
  on lessons (188 vs 177).
- **Goodies** for non-achievers follow `challenge_rewards` on weekly goals.
  Users with 0 goals get nothing.

## Data issues found

| Issue | What I did |
|---|---|
| 8 lessons (1303–1308, 1311, 1314) marked 100% for all 330 users on 26 Oct, most within the same second. That's 4.27 hrs and a full weekly goal for every user. | Removed (2,640 rows). Without this, W7 shows 100% goal completion and 23 extra users "achieve" the target. |
| 4 users with more than 24 hrs of learning in one day (user_013, 224, 266, 292; worst 59 hrs). | Excluded from analysis and rewards. Listed for manual review. |
| One record with 120% overall completion. | Capped at 100. |
| In the xlsx, `challenge_start_date` reads as 9 Dec 2022 and `batch_week` mixes text and dates. | Used the CSV copy of the users table. |

I didn't use an IQR/z-score cut on total hours. In a challenge that rewards
the most hours, the top tail is exactly the group we care about, and an IQR
cut would have removed the winners.

## Known limitations

- Learning time comes from completion %, not watch time. About a quarter of
  credited records arrive faster than the content could be watched, so "hours"
  is best read as content covered.
- No post-challenge data, so retention after 6 Nov can't be measured yet.
