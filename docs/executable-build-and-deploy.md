# Optional Windows Executable

The source + `uv` deployment is the canonical path. Build the executable when the target PC cannot install Python or Git.

## Build locally

On a Windows build machine with Python and `uv`:

```powershell
uv sync --group dev
uv run pyinstaller packaging/barangay_server.spec --clean
```

The output is:

```text
dist/barangay_server.exe
```

The build includes the Flask templates, static assets, migrations, and approved seed DOCX templates. Runtime data is created beside the executable under `data/`.

## Build with GitHub Actions

Workflow: `.github/workflows/build-executable.yml`

- Run it manually from GitHub Actions, or
- Push a version tag such as `v1.0.0` to create a release

The workflow runs the SQLite test suite first, then publishes the executable and a small ZIP bundle.

## Deploy the executable

1. Copy `barangay_server.exe`, `.env.example`, and `start.bat` to a stable folder such as `C:\barangay_system\app`.
2. Copy `.env.example` to `.env` and set a production `SECRET_KEY`.
3. Double-click `start.bat`.
4. Open `http://localhost:5000`.

No Python, Git, database server, or internet connection is required on the target PC after the executable bundle has been built.

## Preserve data during updates

Back up and preserve the executable folder's `data/` directory. Replace the executable and bundled application files, but do not delete `data/`.
