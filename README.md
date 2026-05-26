# Barangay Document Management System

Flask-based barangay office system for resident records, document issuance, reporting, audit logging, and backups.

## Active Architecture

- Backend: Flask
- Server: Waitress (via `run_server.py`)
- Database: MySQL/MariaDB through XAMPP (Windows)
- Frontend: Flask templates + Bootstrap in `frontend/`
- Production port: `5000`
- Document generation: `.docx` template rendering + LibreOffice PDF conversion

## Setup

```bash
pip install uv
uv sync --group dev
cp .env.example .env
```

Configure `.env`:

```env
SECRET_KEY=replace-with-long-random-string
DATABASE_URL=mysql+pymysql://barangay_user:barangay_password@127.0.0.1:3306/barangay_db?charset=utf8mb4
HOST=0.0.0.0
PORT=5000
LIBREOFFICE_BIN=soffice
```

## Run

```bash
uv run python run_server.py
```

Open `http://127.0.0.1:5000`.

## Tests

```bash
uv run python -m pytest tests
```
