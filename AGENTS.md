# cmpManager

User preferred name: Kevin.

This repository implements a six-person company management PWA with isolated personal and company workspaces. Kevin is platform admin and owner of C&K Commerce. Company owners/admins manage all company resources; ordinary members have no implicit company-wide business access. Grant viewer/editor per project or finance profile (including the default ledger). Project grants govern project notes, subsections, files, tasks and stock movements; ledger grants independently govern all ledger transactions, reimbursement and ledger tasks. Company membership alone grants no project/ledger access. Existing legacy editor/viewer memberships migrate to ordinary members without automatic resource grants. Personal workspaces and their controlled maintenance exception remain independent.

Enforce permissions on every read, write, reference, linked creation and audit view. Every mutation and its server-attributed before/after audit must be one transaction. Record moves require access to old and new scopes; history requires access to both snapshots and the current record. Company removal revokes all resource grants. Full ledger access must preserve whole-ledger P&L even without project access; opaque existing project associations may be retained but unauthorized associations cannot be added. Warehouse/product catalogs are shared reference data for project members; movement totals only include authorized projects, and global stock integrity checks stay internal.

Never store or reveal recoverable member passwords. Admins may set/reset initial passwords, and members must change them at first login. Data maintenance is an explicit, session-bound, 15-minute read-only exception requiring administrator password reauthentication and a reason; every access is audited in the member's personal space, and the member may revoke it. Do not add covert bypasses. Scope every business query, audit query, reference validation, and mutation to its authorized workspace; preserve PostgreSQL FORCE RLS.

Never commit real business data, passwords, sessions, OAuth credentials or `.local` files. The GitHub repository is public. Use Cloud SQL for production; never silently fall back to local SQLite on Cloud Run. Deploy only the dedicated `cmpmanager` service; do not auto-detect or replace iPortfolio2.

Run relevant tests with `.venv/bin/python -m pytest -q` and `node --check static/app.js`. Check phone layouts before shipping UI changes. Google Sheets is currently a read-only reference; do not import or change its business records unless requested.
