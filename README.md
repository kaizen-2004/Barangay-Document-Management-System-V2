# Barangay Document Management System

A computer system that helps barangay offices manage resident records, issue official documents, and keep everything organized and secure. Built for **Krus Na Ligas, Quezon City**.

---

## What It Does

### Resident Records
- Store and search resident information (name, address, contact, photo, signature)
- Automatically generates a unique Barangay ID number for each resident (e.g., `KNL-2025-00001`)
- Capture photos directly from a webcam, with automatic background cleanup

### Document Issuance
- Issue the most common barangay documents:
  - Barangay ID
  - Barangay Clearance
  - Business Clearance
  - Certificate of Residency
  - Certificate of Indigency
  - Good Moral Certificate
  - Other custom documents
- Documents are generated as professional PDF files ready for printing
- Each printed document includes a **QR code** for online verification

### Search
- Quickly find any resident or document by typing any part of their name or details

### Audit Trail
- Every action in the system is recorded -- who did what, when, and from which computer
- Useful for accountability and compliance

### Backup and Recovery
- Back up all data with one click from the admin panel
- Restore from a backup if anything goes wrong

### User Management
- Two user roles: **Admin** (full access) and **Clerk** (issue documents, manage residents)
- Password protection, automatic logout after inactivity, and login attempt tracking

---

## How to Use

### Opening the System

1. Start the system (see "Installation" below)
2. Open a web browser (Chrome, Firefox, Edge)
3. Go to: `http://localhost:5000`
4. Log in with your username and password

> On the first run, the system generates a random admin password and prints it once in the server console. Save it and change the password after logging in.

### Daily Tasks

| Task | How To |
|------|--------|
| Add a new resident | Go to **Residents** > **Add New** |
| Find a resident | Use the **Search** bar at the top |
| Issue a document | Go to **Documents** > **Issue New**, select the resident and document type |
| Print a document | After issuing, click **Download** to get the PDF |
| View issued documents | Go to **Documents** to see the full list |
| Take a photo | Open a resident's profile and click **Capture Photo** |

### Admin Tasks

| Task | How To |
|------|--------|
| Add a new user | Go to **Admin** > **Users** > **Add User** |
| Back up the database | Go to **Admin** > **Backups** > **Create Backup** |
| View activity logs | Go to **Admin** > **Audit Logs** |
| Manage document types | Go to **Admin** > **Document Types** |
| Manage streets | Go to **Admin** > **Streets** |
| Manage officials | Go to **Admin** > **Officials** |

---

## Installation

### What You Need

- Python 3.12+
- `uv` package manager
- LibreOffice on the server PC for PDF conversion (optional; DOCX output remains available)

### Quick Setup

1. Install Python and `uv` (one-time setup, requires internet):

```bash
python -m pip install uv
uv sync
```

2. Copy the settings template and edit it:

```bash
copy .env.example .env
```

3. Open the `.env` file in a text editor and set a secret key:

```
SECRET_KEY=any-long-random-text-here
```

> The database is created automatically -- no extra setup needed.

4. Start the system:

```bash
start.bat
```

5. Open `http://localhost:5000` in your browser.

### First-Time Setup

When the system starts for the first time, it automatically:
- Creates all necessary database tables
- Adds 25 local streets
- Adds 7 document types
- Creates an admin account with a generated random password printed in the server console

> **Important:** Change the generated admin password immediately after first login.

---

## Backing Up Your Data

### From the Admin Panel
1. Go to **Admin** > **Backups**
2. Click **Create Backup**
3. The backup file is saved in the `data/backups/` folder

### From the Command Line (for IT staff)

```bash
# Create a backup
uv run flask backup-db

# Restore from a backup
uv run flask restore-db --path data/backups/backup-file.db --yes
```

---

## Document Verification (QR Codes)

Each document you issue includes a QR code. When scanned with a phone camera, it opens a verification page that confirms the document is authentic.

To make QR codes work from any location (not just your office computer):

1. Set up a public link using **Cloudflare Tunnel** or **Google Drive**
2. Add the link to your `.env` file:

```
PUBLIC_URL=https://your-public-link-here
```

See `docs/onprem-windows-deployment-checklist.md` for the LAN deployment checklist.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Can't open the system | Make sure the system is running and no other program is using port 5000 |
| Documents won't generate as PDF | Make sure LibreOffice is installed and the path in `.env` is correct |
| Login not working | Check your username and password. After 5 failed attempts, you'll be temporarily locked out |
| System is slow | Close other programs, or restart the system |

---

## More Information

| Document | What It Covers |
|----------|---------------|
| `docs/onprem-windows-deployment-checklist.md` | Source deployment on a Windows LAN server |
| `docs/executable-build-and-deploy.md` | Optional standalone Windows executable |

---

## For IT Staff

<details>
<summary>Technical details (click to expand)</summary>

### Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python / Flask |
| Database | SQLite (no setup required) |
| Server | Waitress (production) |
| Frontend | HTML templates + Bootstrap CSS |
| Document Generation | docxtpl + LibreOffice |
| Image Processing | OpenCV, Pillow, rembg |

### Project Structure

```
run_server.py                # Start the system
wsgi.py                      # Waitress/WSGI entry point
.env.example                 # Settings template
barangay_project/             # Core application files
frontend/                     # Web pages, styling, and seed DOCX templates
tests/                        # Automated tests
scripts/windows/              # Optional service and backup helpers
data/                         # Runtime database, uploads, and backups
migrations/                   # Database structure changes
```

### CLI Commands

```bash
uv run flask init-db                          # Initialize database
uv run flask backup-db                        # Create backup
uv run flask restore-db --path <file> --yes   # Restore backup
uv run flask purge-expired-documents --months 6   # Archive old documents
```

### Running Tests

```bash
uv run python -m pytest tests
```

### Building a Standalone Windows App

```bash
build.bat
```

Produces `dist/barangay_server.exe` -- runs on any Windows PC without Python or Git installed.

</details>

---

*Barangay Document Management System -- Making barangay office work easier and more organized.*
