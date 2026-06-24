#!/bin/bash
cd /home/newlevel/devel/forestshop/parovanie_produktov
for i in $(seq 1 8); do
  PYTHONPATH=src .venv/bin/python scripts/resolve_urls.py >> data/out/resolve_all.log 2>&1
  if grep -q "RESOLVE DONE" data/out/resolve_all.log; then echo "ALL DONE attempt $i" >> data/out/resolve_all.log; break; fi
  echo "--- attempt $i ended without DONE, retrying ---" >> data/out/resolve_all.log
done
