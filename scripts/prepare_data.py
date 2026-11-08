"""
Champions Challenge - data prep

Reads the raw dataset, cleans it, applies the challenge rules and writes
the flat tables that feed the Looker Studio report (data/processed/).

Run from the project root:
    python scripts/prepare_data.py
"""

import ast
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

CHALLENGE_START = pd.Timestamp("2022-09-12")   # Monday
CHALLENGE_END = pd.Timestamp("2022-11-06")     # Sunday, end of week 8
N_WEEKS = 8

# challenge rules (from the brief)
GOAL_MIN_LESSONS = 5
GOAL_MIN_HOURS = 3.5
TARGET_MIN_GOALS = 5
TARGET_MIN_HOURS = 20

# anything above this in a single calendar day can't be real
MAX_HOURS_PER_DAY = 24
# share of users completing the same lesson on the same day -> bulk/system update
BULK_SHARE = 0.9

PRIZES = {1: "Laptop", 2: "Tablet", 3: "Smart Watch"}


def load():
    xl = pd.ExcelFile(RAW / "Champions_Challenge_Dataset.xlsx")

    # Users come from the CSV export. In the xlsx, Excel parsed 9/12/2022 as
    # 9-Dec and batch_week is a mix of real dates and text, so the CSV is the
    # cleaner copy of this table.
    users = pd.read_csv(RAW / "users_basic_details.csv")
    users["batch_week"] = pd.to_datetime(users["batch_week"], format="%m/%d/%Y")
    users["challenge_start_date"] = pd.to_datetime(users["challenge_start_date"], format="%m/%d/%Y")

    demo = users["demographics"].apply(ast.literal_eval).apply(pd.Series)
    users = pd.concat([users.drop(columns="demographics"), demo], axis=1)
    users = users.rename(columns={"year_of_graduation": "grad_year"})

    act = pd.read_excel(xl, "day_wise_user_learning_activity")
    # second datetime column is just the date part of the first one (checked: 100% match)
    act = act.drop(columns=["activity_datetime.1"])
    act = act.rename(columns={"activity_datetime": "activity_ts"})

    lessons = pd.read_excel(xl, "lesson_details")
    rewards = pd.read_excel(xl, "challenge_rewards")
    return users, act, lessons, rewards


def clean_activity(act, lessons):
    act = act.merge(lessons, on="lesson_id", how="left")
    assert act["lesson_duration_in_mins"].notna().all(), "lesson missing from lesson_details"

    # one row has overall_completion_percentage = 120 (user_256 / lesson_1330)
    n_capped = (act["overall_completion_percentage"] > 100).sum()
    act["overall_completion_percentage"] = act["overall_completion_percentage"].clip(upper=100)

    act["date"] = act["activity_ts"].dt.normalize()
    act["learn_mins"] = act["day_completion_percentage"] / 100 * act["lesson_duration_in_mins"]

    # week index relative to challenge start: -8..-1 = before, 0..7 = during
    act["week_idx"] = (act["date"] - CHALLENGE_START).dt.days // 7
    act["period"] = np.where(act["week_idx"] >= 0, "During", "Before")

    # A lesson counts as completed the first time overall % hits 100.
    # ~530 user-lesson pairs get completed again later (rewatch/revision) -
    # the time still counts as learning but the lesson isn't "new" progress.
    act = act.sort_values(["user_id", "activity_ts"]).reset_index(drop=True)
    hit_100 = act["overall_completion_percentage"] >= 100
    first_hit = ~act[hit_100].duplicated(["user_id", "lesson_id"])
    act["lesson_completed"] = False
    act.loc[first_hit[first_hit].index, "lesson_completed"] = True

    return act, n_capped


def find_bulk_lessons(act, n_users):
    """Lessons that (almost) every user 'completed' on the same day.

    On 26-Oct-2022 ~11:45 all 330 users got 8 lessons (1303-1308, 1311, 1314)
    at 100% within a second or two of each other - 4.27 hrs / 8 lessons each,
    which is a full weekly goal on its own. That's a system update, not
    learning. Rule kept generic so it would catch the same thing next time.
    """
    share = act.groupby(["lesson_id", "date"])["user_id"].nunique() / n_users
    return sorted(share[share >= BULK_SHARE].index.get_level_values("lesson_id").unique())


def find_outliers(act):
    daily = act.groupby(["user_id", "date"], as_index=False)["learn_mins"].sum()
    daily["hours"] = daily["learn_mins"] / 60
    bad_days = daily[daily["hours"] > MAX_HOURS_PER_DAY].sort_values("hours", ascending=False)

    out = (bad_days.groupby("user_id")
           .agg(impossible_days=("date", "count"),
                worst_day=("date", "first"),
                max_hours_in_a_day=("hours", "max"))
           .reset_index())
    out["max_hours_in_a_day"] = out["max_hours_in_a_day"].round(1)
    out["reason"] = f"Logged > {MAX_HOURS_PER_DAY} hrs of learning in one calendar day"
    return out


def weekly_table(act, users):
    # full grid so inactive weeks show up as zeros (needed for retention)
    weeks = pd.DataFrame({"week_idx": range(-N_WEEKS, N_WEEKS)})
    grid = users[["user_id"]].merge(weeks, how="cross")

    wk = (act.groupby(["user_id", "week_idx"])
          .agg(learn_mins=("learn_mins", "sum"),
               lessons_completed=("lesson_completed", "sum"),
               active_days=("date", "nunique"))
          .reset_index())

    wk = grid.merge(wk, on=["user_id", "week_idx"], how="left").fillna(0)
    wk["hours"] = wk["learn_mins"] / 60   # rounded only at export
    wk["lessons_completed"] = wk["lessons_completed"].astype(int)
    wk["active_days"] = wk["active_days"].astype(int)
    wk["is_active"] = (wk["learn_mins"] > 0).astype(int)
    wk["goal_met"] = ((wk["hours"] >= GOAL_MIN_HOURS) &
                      (wk["lessons_completed"] >= GOAL_MIN_LESSONS)).astype(int)

    wk["period"] = np.where(wk["week_idx"] >= 0, "During", "Before")
    wk["week_start"] = CHALLENGE_START + pd.to_timedelta(wk["week_idx"] * 7, unit="D")
    # Pre-8 ... Pre-1 | W1 ... W8  (sort on week_start in the report, not on this)
    wk["week_label"] = np.where(wk["week_idx"] >= 0,
                                "W" + (wk["week_idx"] + 1).astype(str),
                                "Pre" + wk["week_idx"].astype(str))
    wk["week_no"] = np.where(wk["week_idx"] >= 0, wk["week_idx"] + 1, wk["week_idx"])
    return wk.drop(columns="learn_mins")


def target_reached_on(act, user_ids):
    """Date each user satisfied both target conditions (used as a tie-breaker)."""
    rows = []
    during = act[act["period"] == "During"]
    for uid in user_ids:
        d = during[during["user_id"] == uid]
        daily = (d.groupby("date")
                 .agg(m=("learn_mins", "sum"), l=("lesson_completed", "sum"), w=("week_idx", "first"))
                 .reset_index())
        daily["cum_hours"] = daily["m"].cumsum() / 60
        daily["wk_hours"] = daily.groupby("w")["m"].cumsum() / 60
        daily["wk_lessons"] = daily.groupby("w")["l"].cumsum()
        daily["wk_goal_hit"] = (daily["wk_hours"] >= GOAL_MIN_HOURS) & (daily["wk_lessons"] >= GOAL_MIN_LESSONS)
        # count a week's goal from the first day it was hit in that week
        first_hit = daily["wk_goal_hit"] & ~daily.groupby("w")["wk_goal_hit"].shift(fill_value=False)
        daily["goals_so_far"] = first_hit.cumsum()
        ok = daily[(daily["cum_hours"] >= TARGET_MIN_HOURS) & (daily["goals_so_far"] >= TARGET_MIN_GOALS)]
        rows.append({"user_id": uid, "target_reached_on": ok["date"].min() if len(ok) else pd.NaT})
    return pd.DataFrame(rows)


def user_table(wk, users, rewards, act):
    def summarise(period):
        p = wk[wk["period"] == period]
        s = p.groupby("user_id").agg(hours=("hours", "sum"),
                                      lessons=("lessons_completed", "sum"),
                                      active_weeks=("is_active", "sum"),
                                      active_days=("active_days", "sum"),
                                      goals=("goal_met", "sum"))
        return s.add_prefix("pre_" if period == "Before" else "")

    u = users.set_index("user_id").join(summarise("Before")).join(summarise("During"))
    u = u.rename(columns={"hours": "challenge_hours", "lessons": "challenge_lessons",
                          "goals": "weekly_goals"})

    # longest run of consecutive weekly goals during the challenge
    def longest_streak(s):
        best = cur = 0
        for v in s:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        return best

    during = wk[wk["period"] == "During"].sort_values("week_idx")
    u["goal_streak"] = during.groupby("user_id")["goal_met"].apply(longest_streak)
    last_active = during[during["is_active"] == 1].groupby("user_id")["week_no"].max()
    u["last_active_week"] = last_active.astype("Int64")
    u["active_in_final_week"] = (u["last_active_week"] == N_WEEKS).fillna(False).astype(int)

    # how the first two weeks went - turned out to be a strong early signal
    u["goals_first_2_weeks"] = during[during["week_no"] <= 2].groupby("user_id")["goal_met"].sum()
    u["first_2_weeks"] = u["goals_first_2_weeks"].astype(int).astype(str) + " of 2 goals"

    u["target_achieved"] = ((u["weekly_goals"] >= TARGET_MIN_GOALS) &
                            (u["challenge_hours"] >= TARGET_MIN_HOURS)).astype(int)

    # ranking for the top 3 prizes. Tie-break order:
    #   1. challenge hours  2. weekly goals  3. lessons completed in challenge
    #   4. earliest date the target was reached
    achievers = u[u["target_achieved"] == 1].reset_index()
    reached = target_reached_on(act, achievers["user_id"])
    achievers = achievers.merge(reached, on="user_id")
    achievers["hours_r"] = achievers["challenge_hours"].round(2)
    achievers = achievers.sort_values(["hours_r", "weekly_goals", "challenge_lessons", "target_reached_on"],
                                      ascending=[False, False, False, True])
    achievers["rank"] = range(1, len(achievers) + 1)
    u = u.join(achievers.set_index("user_id")[["rank", "target_reached_on"]])
    u["rank"] = u["rank"].astype("Int64")

    reward_map = dict(zip(rewards["total_weekly_goals_achieved"], rewards["reward"]))
    max_tier = rewards["total_weekly_goals_achieved"].max()

    def reward_for(r):
        if r["target_achieved"]:
            prize = PRIZES.get(int(r["rank"]))
            return f"{prize} + Hamper" if prize else "Hamper"
        if r["weekly_goals"] == 0:
            return "No reward"
        # a non-achiever can have at most 5 goals (6 goals x 3.5h = 21h > 20h),
        # but cap anyway in case the rules change
        return reward_map[min(int(r["weekly_goals"]), max_tier)]

    u["reward"] = u.apply(reward_for, axis=1)
    u["reward_type"] = np.select(
        [(u["rank"] <= 3).fillna(False).astype(bool), u["target_achieved"] == 1, u["reward"] != "No reward"],
        ["Top 3 prize", "Target hamper", "Consolation goodie"], "None")

    u["hours_change"] = u["challenge_hours"] - u["pre_hours"]
    u["hours_change_pct"] = np.where(u["pre_hours"] > 0,
                                     u["hours_change"] / u["pre_hours"], np.nan)
    u["avg_weekly_hours"] = u["challenge_hours"] / N_WEEKS
    u["pre_avg_weekly_hours"] = u["pre_hours"] / N_WEEKS

    u["engagement_segment"] = segment_users(u)
    u["outcome_group"] = outcome_group(u)
    return u.reset_index()


def segment_users(u):
    """Before-vs-during movement. 'High' = above the median weekly hours of the
    pre-challenge period, so both periods are judged against the same bar."""
    bar = u["pre_avg_weekly_hours"].median()
    hi_before = u["pre_avg_weekly_hours"] >= bar
    hi_during = u["avg_weekly_hours"] >= bar
    return np.select(
        [hi_before & hi_during, ~hi_before & hi_during, hi_before & ~hi_during],
        ["Consistent high", "Activated", "Dropped off"], "Consistently low")


def outcome_group(u):
    near_miss = (u["target_achieved"] == 0) & (
        (u["weekly_goals"] == TARGET_MIN_GOALS - 1) |
        ((u["weekly_goals"] >= TARGET_MIN_GOALS) & (u["challenge_hours"] < TARGET_MIN_HOURS)))
    return np.select(
        [u["target_achieved"] == 1, near_miss, u["weekly_goals"] >= 2],
        ["Achiever", "Near miss", "Partial (2-3 goals)"], "Low (0-1 goal)")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    users, act, lessons, rewards = load()
    n_rows_raw = len(act)
    act, n_capped = clean_activity(act, lessons)

    bulk = find_bulk_lessons(act, users["user_id"].nunique())
    is_bulk = act["lesson_id"].isin(bulk)
    bulk_rows = act[is_bulk]
    act = act[~is_bulk].reset_index(drop=True)
    print(f"bulk-credited lessons removed: {bulk} ({len(bulk_rows)} rows)")

    outliers = find_outliers(act)
    outliers = outliers.merge(users[["user_id", "state", "gender", "branch"]], on="user_id")
    bad = set(outliers["user_id"])
    print(f"capped {n_capped} row(s) with completion > 100%")
    print(f"excluding {len(bad)} outlier users: {sorted(bad)}")

    dq_log = pd.DataFrame([
        {"issue": "Bulk system completion",
         "detail": f"{len(bulk)} lessons ({', '.join(bulk)}) marked 100% for every user on "
                   f"26/27-Oct-2022, mostly within the same second; "
                   f"{bulk_rows['learn_mins'].sum() / 60 / users['user_id'].nunique():.2f} hrs per user",
         "rows_affected": len(bulk_rows), "users_affected": bulk_rows["user_id"].nunique(),
         "action": "Rows removed"},
        {"issue": "Impossible learning hours",
         "detail": f"> {MAX_HOURS_PER_DAY} hrs of learning credited in one calendar day",
         "rows_affected": int(act["user_id"].isin(bad).sum()), "users_affected": len(bad),
         "action": "Users excluded from analysis and rewards (send for manual review)"},
        {"issue": "Completion above 100%",
         "detail": "overall_completion_percentage = 120 (user_256, lesson_1330)",
         "rows_affected": int(n_capped), "users_affected": 1, "action": "Capped at 100"},
        {"issue": "Date parsing in xlsx",
         "detail": "challenge_start_date read as 2022-12-09 and batch_week mixed text/dates",
         "rows_affected": len(users), "users_affected": len(users),
         "action": "Used the CSV copy of users_basic_details"},
    ])
    dq_log.insert(0, "raw_rows", n_rows_raw)

    users = users[~users["user_id"].isin(bad)].reset_index(drop=True)
    act = act[~act["user_id"].isin(bad)].reset_index(drop=True)

    wk = weekly_table(act, users)
    u = user_table(wk, users, rewards, act)

    demo_cols = ["user_id", "state", "gender", "branch", "grad_year", "batch_week"]
    wk = wk.merge(users[demo_cols], on="user_id")
    wk = wk.merge(u[["user_id", "target_achieved", "outcome_group"]], on="user_id")

    # daily rows - used for the day-of-week / time trend charts
    daily = (act.groupby(["user_id", "date", "period", "week_idx"])
             .agg(learn_mins=("learn_mins", "sum"),
                  lessons_completed=("lesson_completed", "sum"),
                  records=("lesson_id", "size"))
             .reset_index())
    daily["hours"] = (daily["learn_mins"] / 60).round(3)
    daily["weekday"] = daily["date"].dt.day_name()
    daily["weekday_no"] = daily["date"].dt.dayofweek + 1
    # "1 Mon" ... "7 Sun" so Looker sorts the days correctly
    daily["day_label"] = daily["weekday_no"].astype(str) + " " + daily["weekday"].str[:3]
    daily = daily.drop(columns="learn_mins").merge(users[demo_cols], on="user_id")

    # hour-of-day profile, already aggregated (keeps the sheet small)
    act["hour"] = act["activity_ts"].dt.hour
    act["weekday"] = act["activity_ts"].dt.day_name()
    act["weekday_no"] = act["activity_ts"].dt.dayofweek + 1
    hourly = (act.groupby(["period", "weekday_no", "weekday", "hour"])
              .agg(hours=("learn_mins", lambda s: round(s.sum() / 60, 2)),
                   users=("user_id", "nunique"))
              .reset_index())
    hourly["day_label"] = hourly["weekday_no"].astype(str) + " " + hourly["weekday"].str[:3]

    # reward procurement list
    reward_list = (u.assign(item=u["reward"].str.split(" \\+ "))
                   .explode("item")
                   .query("item != 'No reward'")
                   .groupby(["item"]).size().rename("quantity").reset_index())
    order = ["Laptop", "Tablet", "Smart Watch", "Hamper"] + list(rewards["reward"][::-1])
    reward_list["sort"] = reward_list["item"].map({k: i for i, k in enumerate(order)})
    reward_list = reward_list.sort_values("sort").drop(columns="sort")

    funnel = pd.DataFrame({
        "step": ["Valid users", "Active during challenge", "Hit at least 1 weekly goal",
                 "Hit 4+ weekly goals", "Achieved target"],
        "users": [len(u), int((u["challenge_hours"] > 0).sum()), int((u["weekly_goals"] >= 1).sum()),
                  int((u["weekly_goals"] >= 4).sum()), int(u["target_achieved"].sum())],
    })
    funnel["pct_of_valid"] = (funnel["users"] / len(u)).round(4)

    winners = (u[u["rank"] <= 3]
               .sort_values("rank")
               [["rank", "user_id", "state", "gender", "branch", "grad_year", "challenge_hours",
                 "weekly_goals", "challenge_lessons", "target_reached_on", "reward"]])

    # tidy up for export
    u["batch_week"] = u["batch_week"].dt.date
    u["challenge_start_date"] = u["challenge_start_date"].dt.date
    for c in ["challenge_hours", "pre_hours", "hours_change", "avg_weekly_hours", "pre_avg_weekly_hours"]:
        u[c] = u[c].round(2)
    u["hours_change_pct"] = u["hours_change_pct"].round(4)
    wk["hours"] = wk["hours"].round(3)
    winners["challenge_hours"] = winners["challenge_hours"].round(2)

    files = {
        "users": u, "user_weeks": wk, "user_days": daily, "hourly_profile": hourly,
        "winners": winners, "reward_list": reward_list, "funnel": funnel, "outliers": outliers,
        "data_quality_log": dq_log,
    }
    for name, df in files.items():
        df.to_csv(OUT / f"{name}.csv", index=False)

    # one workbook with every table as a tab -> upload to Drive, open with Google Sheets
    with pd.ExcelWriter(OUT / "champions_challenge_model.xlsx") as xw:
        for name, df in files.items():
            df.to_excel(xw, sheet_name=name, index=False)

    print(f"users kept: {len(u)} | achievers: {u['target_achieved'].sum()}")
    print(winners.to_string(index=False))


if __name__ == "__main__":
    main()
