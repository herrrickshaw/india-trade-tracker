#!/bin/bash
# Refresh CSV snapshots from the DuckDB, then commit+push data/ if anything changed.
# Called by cron after each collector run.
set -e
cd "$(dirname "$0")/.."

/usr/bin/python3 - << 'EOF'
import duckdb
con = duckdb.connect('data/trade.duckdb')
con.execute("COPY eidb_chapter_trade TO 'data/eidb_chapter_trade_snapshot.csv' (HEADER)")
con.execute("COPY worldbank_trade TO 'data/worldbank_trade_snapshot.csv' (HEADER)")
EOF

git add data/
if git diff --cached --quiet; then
    echo "$(date '+%F %T') sync: no data changes"
else
    git commit -q -m "data: automated refresh $(date '+%F')"
    git push -q
    echo "$(date '+%F %T') sync: committed and pushed"
fi
