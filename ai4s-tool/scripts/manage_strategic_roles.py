"""Grant/revoke maintenance roles from the trusted server shell only."""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai4s_tool.api.strategic_access import grant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--visitor", required=True)
    parser.add_argument("--role", choices=["maintainer", "editor", "reviewer", "revoked"], required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("仅操作已经备份的现有数据库")
    with sqlite3.connect(args.db, timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        grant(conn, args.visitor, args.role, operator=args.operator, reason=args.reason)
    print("维护角色已记录；下次请求生效，不改团队或历史研判。")


if __name__ == "__main__":
    main()
