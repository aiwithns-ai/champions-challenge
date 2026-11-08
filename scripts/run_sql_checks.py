"""Load the raw tables into SQLite, run sql/challenge_checks.sql and compare
a few headline numbers with what prepare_data.py wrote out."""

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

xl = pd.ExcelFile(RAW / "Champions_Challenge_Dataset.xlsx")
activity = pd.read_excel(xl, "day_wise_user_learning_activity").drop(columns=["activity_datetime.1"])
activity = activity.rename(columns={"activity_datetime": "activity_ts"})
activity["activity_ts"] = activity["activity_ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
lessons = pd.read_excel(xl, "lesson_details")
users = pd.read_csv(RAW / "users_basic_details.csv")[["user_id"]]

con = sqlite3.connect(":memory:")
activity.to_sql("activity", con, index=False)
lessons.to_sql("lessons", con, index=False)
users.to_sql("users", con, index=False)

sql = (ROOT / "sql" / "challenge_checks.sql").read_text()
setup, *checks = sql.split("-- @check ")
con.executescript(setup)

results = {}
for block in checks:
    name, query = block.split("\n", 1)
    results[name.strip()] = pd.read_sql(query, con)
    print(f"\n[{name.strip()}]")
    print(results[name.strip()].to_string(index=False))

# compare with python output
u = pd.read_csv(OUT / "users.csv")
py_ach = int(u["target_achieved"].sum())
sql_ach = int(results["achievers"]["n"].iloc[0])
print(f"\nachievers  python={py_ach}  sql={sql_ach}  {'OK' if py_ach == sql_ach else 'MISMATCH'}")

py_top = u.nsmallest(3, "rank")["user_id"].tolist()
sql_top = results["top5"]["user_id"].tolist()[:3]
print(f"top 3 set  python={sorted(py_top)}  sql={sorted(sql_top)}  "
      f"{'OK' if set(py_top) == set(sql_top) else 'MISMATCH'}")
