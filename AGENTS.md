# cmpManager

User preferred name: Kevin.

This repository implements a six-person company management PWA. All six members may read and update financial records. Kevin administers individual username/password accounts. Keep audit attribution server-controlled and save every business mutation together with its before/after audit record in one transaction.

Never commit real business data, passwords, sessions, OAuth credentials or `.local` files. The GitHub repository is public. Use Cloud SQL for production; never silently fall back to local SQLite on Cloud Run. Deploy only the dedicated `cmpmanager` service; do not auto-detect or replace iPortfolio2.

Run relevant tests with `.venv/bin/python -m pytest -q` and `node --check static/app.js`. Check phone layouts before shipping UI changes. Google Sheets is currently a read-only reference; do not import or change its business records unless requested.
