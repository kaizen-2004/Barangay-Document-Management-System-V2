Barangay System – Windows Deployment (One-Click)

Prerequisites:
  (none – the setup script installs everything automatically)

Steps:

  1. Extract the project folder to your desired location, e.g.:
     C:\barangay_system\app

  2. Double-click  packaging\windows_bundle\SETUP.bat

     This single script will:
       - Install Python 3.12 (via winget, no manual download)
       - Install the uv package manager
       - Generate a secure .env configuration
       - Install all dependencies (including AI photo processing)
       - Create the SQLite database + seed data (no MySQL needed)
       - Register a Windows service named "BarangaySystem"
       - Add a firewall rule for port 5000
       - Start the service (auto-starts on boot)
       - Create a desktop shortcut
       - Open the system in your browser

     First run takes ~3 minutes (PyTorch download). Subsequent
     runs are faster.

  3. Log in at http://localhost:5000
     Default credentials: admin / admin
     Change the password immediately.

  4. Access from other computers on the same network at:
     http://<server-pc-ip>:5000

Updating to a new version:

  1. Replace the project files (git pull, or extract new zip
     over the existing folder).

  2. Double-click  packaging\windows_bundle\UPDATE.bat

     This will update dependencies and restart the service
     in ~10 seconds.

Managing the service:

  - Start:   net start BarangaySystem
  - Stop:    net stop BarangaySystem
  - Restart: net stop BarangaySystem && net start BarangaySystem
  - Or use:  services.msc  (find "BarangaySystem")
  - Logs:    data\logs\service-out.log  +  data\logs\service-err.log

Backup:

  Simply copy the file  data\barangay.db  somewhere safe.
  All data is in that single file.

  For automated daily backups, run (as Administrator):
    packaging\windows_bundle\INSTALL_BACKUP_TASK.bat

Recovery:

  To restore from a backup, stop the service, replace
  data\barangay.db with your backup copy, then start the
  service again.
