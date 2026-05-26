# Barangay Project - Flask Setup

## 1) Setup

```bash
pip install uv
uv sync --group dev
cp .env.example .env
```

Edit `.env` with at least:

```env
SECRET_KEY=replace-with-long-random-string
DATABASE_URL=mysql+pymysql://barangay_user:barangay_password@127.0.0.1:3306/barangay_db?charset=utf8mb4
HOST=0.0.0.0
PORT=5000
LIBREOFFICE_BIN=soffice
DOCUMENT_STORAGE_ROOT=C:\\barangay_system\\data
```

## 2) Database

Use Flask migration/initialization commands:

```bash
uv run flask --app barangay_project.app:create_app db upgrade
```

## 3) Run

```bash
uv run python run_server.py
```

Open `http://127.0.0.1:5000`.

## 4) Windows Notes

- Install LibreOffice on the server PC.
- Ensure `soffice` is in PATH or set `LIBREOFFICE_BIN` to full path.
- DOCX templates are required for issued document generation.
- Template files and generated documents are saved under `DOCUMENT_STORAGE_ROOT` (user-controlled path).
