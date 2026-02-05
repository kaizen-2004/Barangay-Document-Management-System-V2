# Barangay Project – Setup & New Features

## 1) Setup

```bash
# inside the project folder
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your real DATABASE_URL and SECRET_KEY
```

### PostgreSQL quick check
```bash
psql "$DATABASE_URL" -c "\conninfo" 
```

## 2) Initialize DB

```bash
flask --app wsgi init-db
```

This will:
- create missing tables
- run "schema-heal" that adds missing columns safely (users, documents.doc_type, transaction_logs extras, etc.)

## 3) Run

```bash
flask --app wsgi run
```

Open: http://127.0.0.1:5000

## 4) What’s new

- Dashboard charts (always visible)
- Reports page with export: CSV / XLSX / PDF
- Audit logging:
  - logins/logouts
  - admin user create/edit/delete
  - PDF downloads
  - report exports

## 5) Ops & reliability

- Health check: `GET /healthz` (returns JSON + DB connectivity)
- Automated backups: `flask --app wsgi backup-db` (uses `BACKUP_DIR`, retention via `BACKUP_RETENTION_DAYS`)
- Restore from backup: `flask --app wsgi restore-db --path /path/to/backup.dump --yes`
- Purge expired documents (issue date + validity):  
  `flask --app wsgi purge-expired-documents --dry-run`  
  `flask --app wsgi purge-expired-documents --yes`
- Structured logging: set `LOG_JSON=True` (default) and `LOG_LEVEL=INFO`
- Error reporting: set `ERROR_REPORT_EMAIL` plus your mail settings to receive unhandled exception reports
- Auto-migrate on deploy: set `AUTO_MIGRATE=True` to run `flask db upgrade` on startup

## 6) Testing

```bash
pip install -r requirements-dev.txt
pytest
```
