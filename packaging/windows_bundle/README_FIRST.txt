Barangay Server Plug-and-Play (Windows)

1) Extract this zip to a folder, for example:
   C:\barangay_system\app

2) Open .env and set at least:
   SECRET_KEY=<long-random-string>
   DATABASE_URL=sqlite:///barangay_mvp.sqlite
   BACKUP_DIR=backups

3) Start server:
   - Double-click START_SERVER.bat

4) Open from client computers:
   http://<server-pc-ip>:5000

5) Optional (auto-start on reboot):
   - Double-click INSTALL_SERVICE.bat (Run as Administrator)

Notes:
- Keep this folder on a dedicated server PC.
- Keep daily backup copies in the backups folder.
- Default admin may be admin/admin on fresh database; change immediately.
