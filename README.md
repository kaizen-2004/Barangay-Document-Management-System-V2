# Barangay Document Management System (Flask + PostgreSQL)

Manage barangay residents and issue official documents (Barangay ID/Clearance, Residency, Indigency, etc.).

Includes:
- Authentication (Flask-Login)
- Roles (`admin`, `clerk`)
- Admin UI for users + document types
- PDF generation for issued documents

## Prerequisites

* Python 3.10+ (works on 3.13 as well)
* PostgreSQL server (for production; SQLite can be used temporarily for testing)
* Pipenv or virtualenv (optional but recommended for dependency management)

## Getting Started

1. **Clone or download this repository.**
2. **Set up a virtual environment** (optional but recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   ```
3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure your database** (recommended):
   ```bash
   export DATABASE_URL="postgresql://USER:PASSWORD@localhost:5432/barangay_db"
   ```

5. **Create/upgrade tables**

   **Option A (recommended): Alembic migrations**
   ```bash
   flask --app barangay_project.app:create_app db upgrade
   ```

   **Option B (quick local run): auto-create tables**
   ```bash
   export AUTO_CREATE_DB=true
   flask --app barangay_project.app:create_app run
   ```

6. **Run the application**:
   ```bash
   flask --app barangay_project.app:create_app run
   ```
   Open your browser at `http://localhost:5000` to see the dashboard.

## Default Login

On a fresh database, the app seeds a default admin:

- **username:** `admin`
- **password:** `admin`

Change this password after your first login.

## Ops & reliability

- Health check: `GET /healthz` (JSON + DB connectivity)
- Automated backups: `flask --app wsgi backup-db` (uses `BACKUP_DIR` + `BACKUP_RETENTION_DAYS`)
- Structured logging: set `LOG_JSON=True` (default) and `LOG_LEVEL=INFO`
- Error reporting: set `ERROR_REPORT_EMAIL` plus your mail settings to receive unhandled exception reports
- Auto-migrate on deploy: set `AUTO_MIGRATE=True` to run Alembic upgrades on startup

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

## File Structure

```
barangay_project/
├── app.py            # Entry point; initializes Flask and registers blueprints
├── config.py         # Configuration classes for different environments
├── forms.py          # WTForms classes for residents and documents
├── models.py         # SQLAlchemy models representing the database schema
├── routes.py         # Application routes and business logic
├── requirements.txt  # Python dependencies
├── migrations/       # Database migration scripts (managed by Flask-Migrate)
├── templates/
│   ├── base.html     # Base layout template with navigation
│   ├── index.html    # Dashboard page
│   ├── residents.html# List residents page
│   ├── resident_form.html  # Add resident form
│   ├── documents.html# List issued documents page
│   └── document_form.html  # Issue document form
└── README.md         # Project overview and setup instructions
```

## Notes

* The project uses **one shared SQLAlchemy instance** (`barangay_project/extensions.py`). If you see errors like *"The current Flask app is not registered with this 'SQLAlchemy' instance"*, it usually means multiple `SQLAlchemy()` objects were created.
* If you already have an existing DB with data and you update the code, you can use `UPGRADE_DB.sql` to apply safe, additive schema changes (it won't error if the tables don't exist).
