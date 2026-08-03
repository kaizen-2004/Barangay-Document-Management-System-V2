# Windows LAN Deployment Checklist

This is the canonical source deployment path for a barangay office:

- One dedicated Windows server PC
- Python + `uv`
- SQLite database stored under `data/`
- LAN-only access from office computers
- Daily local database backups

## 1. Prepare the server PC

Install Python 3.12 or newer, then install `uv`:

```powershell
python -m pip install uv
```

Copy the repository to a stable location such as `C:\barangay_system\app`.
Git is optional after the repository has been copied; updates can also be delivered as a ZIP archive.

## 2. Install the application

Open Command Prompt in the project folder:

```powershell
uv sync
copy .env.example .env
```

Edit `.env` and set a long random `SECRET_KEY`. Keep the SQLite defaults unless the office has a specific storage requirement.

Start the application:

```powershell
start.bat
```

Open `http://localhost:5000`. The first start creates the database and prints the generated admin password in the server console. Change it immediately after login.

## 3. LAN access

On the server PC, run `setup-firewall.bat` as Administrator. Find the server IP with:

```powershell
ipconfig
```

Other office computers can then open:

```text
http://SERVER-IP:5000
```

Keep port 5000 limited to the local network. Do not expose the application directly to the public internet.

## 4. Optional Windows service

The application can run from `start.bat` without a service manager. For automatic startup, install NSSM separately and add `nssm.exe` to `PATH`, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
```

The service setup creates the required `data/` directories, installs dependencies, creates the database, configures the firewall, and starts `BarangaySystem`.

## 5. Backups

From the admin panel, create a backup and copy the resulting database backup to an external drive.

For a scheduled daily copy, run PowerShell as Administrator:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-backup-task.ps1 -SourceDb "C:\barangay_system\app\data\barangay.db" -BackupDir "C:\barangay_system\backups" -RunAt "18:00"
```

Keep at least 14 daily backups and periodically test restoring one.

## 6. Go-live smoke test

- Admin login works and the generated password was changed
- Clerk login works
- Resident create, edit, search, and photo capture work
- Document issue and download work
- Reports export works
- Backup creates a file outside the application folder
- `/healthz` returns an `ok` response

## 7. Updating a source deployment

Stop the application, replace the source files with the new repository/archive, then run:

```powershell
uv sync
start.bat
```

Never replace the `data/` directory during an update. Back it up first.
