# cmpManager

User preferred name: Kevin.

This repository implements a six-person company management PWA with isolated personal and team workspaces. Kevin is platform admin and owner of the C&K Commerce team. Team owners manage membership; editors may read/write business records; viewers may only read. Each user's personal workspace is normally accessible only to that user, including against platform admins. Legacy members migrate as editors, then owners may change their team privileges. Keep audit attribution server-controlled and save every business mutation together with its before/after audit record in one transaction.

Never store or reveal recoverable member passwords. Admins may set/reset initial passwords, and members must change them at first login. Data maintenance is an explicit, session-bound, 15-minute read-only exception requiring administrator password reauthentication and a reason; every access is audited in the member's personal space, and the member may revoke it. Do not add covert bypasses. Scope every business query, audit query, reference validation, and mutation to its authorized workspace; preserve PostgreSQL FORCE RLS.

Never commit real business data, passwords, sessions, OAuth credentials or `.local` files. The GitHub repository is public. Use Cloud SQL for production; never silently fall back to local SQLite on Cloud Run. Deploy only the dedicated `cmpmanager` service; do not auto-detect or replace iPortfolio2.

Run relevant tests with `.venv/bin/python -m pytest -q` and `node --check static/app.js`. Check phone layouts before shipping UI changes. Google Sheets is currently a read-only reference; do not import or change its business records unless requested.
