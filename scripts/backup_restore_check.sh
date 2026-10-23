#!/bin/bash
# Backup/restore rehearsal on a fresh demo install: (A) copy the data folder (laptop), (B) pg_dump/pg_restore +
# blobs (server path). Usage: scripts/backup_restore_check.sh [work-folder]. Prints "identical results" twice.
set -e
cd "$(dirname "$0")/.."
B=${1:-$(mktemp -d)}  # work folder (deleted contents are recreated)
BIN=$PWD/.venv/lib/python3.13/site-packages/pixeltable_pgserver/pginstall18/bin
export KS_MODEL_CACHE=$PWD/data/models
rm -rf $B/orig $B/backup $B/restoredA $B/restoredB $B/*.json $B/ks.dump
serve() { KS_DATA_DIR=$1 nohup uv run knowledge-search serve --port $2 > $1.log 2>&1 & echo $! > $1.pid
          timeout 300 bash -c "until curl -s localhost:$2/readyz | grep -q '\"ready\"'; do sleep 2; done"; }
stop() { kill $(cat $1.pid); sleep 1; pkill -P $(cat $1.pid) 2>/dev/null || true
         timeout 60 bash -c "while ls $1/pg/postmaster.pid >/dev/null 2>&1; do sleep 1; done"; }
probe() { # login as alice, run two searches, save outcome + top titles
  T=$(curl -s -X POST localhost:$1/api/auth/login -H 'content-type: application/json' \
       -d '{"username":"alice","password":"demo-password-alice"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
  for q in "ERR-PAY-4012" "per diem for domestic travel"; do
    curl -s localhost:$1/api/search -H "Authorization: Bearer $T" -H 'content-type: application/json' -d "{\"query\":\"$q\"}" \
      | python3 -c "import json,sys;d=json.load(sys.stdin);print(json.dumps([d['outcome'],[(r['title'],r['chunk_id']) for r in d['results'][:5]]]))"
  done > $2; curl -s localhost:$1/api/documents -H "Authorization: Bearer $T" | python3 -c "import json,sys;print(len(json.load(sys.stdin)), 'documents visible to alice')" >> $2; }

echo "== original install"; serve $B/orig 8010
KS_DATA_DIR=$B/orig uv run knowledge-search demo > /dev/null 2>&1
probe 8010 $B/orig.json; cat $B/orig.json

echo "== (A) laptop: stop, copy the data folder, delete the original, restore the copy elsewhere"
stop $B/orig; cp -a $B/orig $B/backup; rm -rf $B/orig
cp -a $B/backup $B/restoredA; serve $B/restoredA 8011; probe 8011 $B/restoredA.json
diff $B/orig.json $B/restoredA.json && echo "(A) identical results after restore"

echo "== (B) server: pg_dump while running, restore into an empty database + copy blobs"
uri() { uv run python -c "from knowledge_search import db; from pathlib import Path
db.start_embedded(Path('$1'), database='postgres'); print(db._embedded.get_uri('$2'))" 2>/dev/null | grep postgresql; }
$BIN/pg_dump -Fc -d "$(uri $B/restoredA/pg ks)" -f $B/ks.dump; ls -la $B/ks.dump
stop $B/restoredA
mkdir -p $B/restoredB; cp -a $B/backup/blobs $B/restoredB/blobs
KS_DATA_DIR=$B/restoredB uv run python -c "
import subprocess, psycopg; from pathlib import Path; from knowledge_search import db
db.start_embedded(Path('$B/restoredB/pg'), database='ks')   # creates the empty database
r = subprocess.run(['$BIN/pg_restore', '-d', db._embedded.get_uri('ks'), '$B/ks.dump'])
print('pg_restore exit', r.returncode)" 2>&1 | grep -v -i warn
serve $B/restoredB 8012; probe 8012 $B/restoredB.json
diff $B/restoredA.json $B/restoredB.json && echo "(B) identical results after dump/restore"
stop $B/restoredB
