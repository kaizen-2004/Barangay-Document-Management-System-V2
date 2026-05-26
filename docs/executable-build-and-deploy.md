# Executable Build and Deployment (Windows)

This project can be packaged as a Windows executable (`barangay_server.exe`) using PyInstaller.

## GitHub Actions build

Workflow file: `.github/workflows/build-executable.yml`

Trigger methods:

- Manual run: GitHub Actions -> "Build Windows Executable" -> Run workflow
- Tag push: push a tag like `v1.0.0`

When triggered by a `v*` tag, the workflow also creates a GitHub Release and attaches build files automatically.

Outputs:

- `dist/barangay_server.exe`
- `barangay_server_windows_plug_and_play.zip` (exe + start scripts + env sample + helper scripts)

## Local build (optional)

```bash
uv sync --group dev
uv run pyinstaller packaging/barangay_server.spec --clean
```

Executable output:

- `dist/barangay_server.exe`

## Run executable on server PC

1. Copy `barangay_server.exe` to your server folder (example `C:\barangay_system\app`)
2. Copy `.env.example` to `.env` and set production values
3. Ensure XAMPP MySQL is installed and running, then run `SETUP_DATABASE.bat` from the bundle.
4. Run:

```powershell
set APP_ENV=production
set HOST=0.0.0.0
set PORT=5000
barangay_server.exe
```

For Windows service and scheduled backups, follow:

- `docs/onprem-windows-deployment-checklist.md`
