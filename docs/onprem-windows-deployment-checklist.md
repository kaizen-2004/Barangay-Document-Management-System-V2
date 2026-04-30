# On-Prem Windows Deployment Checklist (No Docker)

This checklist is the approved MVP deployment path:

- Single dedicated Windows server PC
- Local SQLite database file (no separate DB server required)
- LAN-only access from up to 5 office computers
- Daily local automated backups

## 1) Prepare server machine

- Assign static LAN IP (example: `192.168.1.20`)
- Create folders:
  - `C:\barangay_system\app`
  - `C:\barangay_system\backups`
  - `C:\barangay_system\logs`
- Install:
  - NSSM (service manager)

## 2) Deploy executable bundle

```bash
cd C:\barangay_system\app
# Copy barangay_server.exe here
```

## 3) Configure environment

Create `.env` in `C:\barangay_system\app` with at least:

```env
SECRET_KEY=replace-with-long-random-string
DATABASE_URL=sqlite:///barangay_mvp.sqlite
AUTO_CREATE_DB=True
AUTO_MIGRATE=False
LOG_LEVEL=INFO
LOG_JSON=True
BACKUP_DIR=C:\barangay_system\backups
ADMIN_MFA_REQUIRED=False
SESSION_COOKIE_SECURE=False
```

## 4) Start and verify app

```bash
barangay_server.exe
```

Open `http://localhost:5000/healthz` and confirm status is `ok`.

From another office PC, open `http://<server-ip>:5000`.

## 6) Register Windows service (NSSM)

- Service name: `BarangaySystem`
- Application path: path to `uv.exe`
- Arguments: empty (if using the executable)
- Startup directory: `C:\barangay_system\app`
- Startup type: `Automatic`

After setup, reboot once and verify service auto-start.

Helper script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/install-service.ps1 -AppDir C:\barangay_system\app -ExecutablePath C:\barangay_system\app\barangay_server.exe
```

## 7) Firewall and access control

- Allow inbound TCP `5000` only from local subnet
- Keep server login access restricted to authorized staff
- Do not expose port 5000 to the public internet

## 8) Backups and restore drill

Set a daily scheduled task:

```bash
powershell -ExecutionPolicy Bypass -Command "`$ts=Get-Date -Format yyyyMMdd_HHmmss; Copy-Item C:\barangay_system\app\barangay_mvp.sqlite C:\barangay_system\backups\backup_`$ts.sqlite"
```

Helper script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/install-backup-task.ps1 -DatabasePath C:\barangay_system\app\barangay_mvp.sqlite -BackupDir C:\barangay_system\backups -RunAt 18:00
```

- Retention target: minimum 14 daily backups
- Weekly: copy latest backup to external drive
- Monthly: test restore with:

```bash
copy /Y C:\barangay_system\backups\<backup-file> C:\barangay_system\app\barangay_mvp.sqlite
```

Or run restore drill helper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/run-restore-drill.ps1 -AppDir C:\barangay_system\app -BackupPath C:\barangay_system\backups\<backup-file>
```

## 9) Go-live smoke test

- Admin login works and password changed from default
- Clerk login works
- Resident create/edit/search works
- Document issue + PDF download works
- Reports export (CSV/XLSX/PDF) works
- Backup command produces file in backup directory
- Audit logs page loads
