Barangay Server Plug-and-Play (Windows)

1) Extract this zip to a folder, for example:
   C:\barangay_system\app

2) Open .env and set at least:
   SECRET_KEY=<long-random-string>
   DATABASE_URL=mysql+pymysql://barangay_user:barangay_password@127.0.0.1:3306/barangay_db?charset=utf8mb4
   BACKUP_DIR=backups

3) Ensure XAMPP MySQL is installed and running, then setup DB/user:
   - Double-click SETUP_DATABASE.bat

4) Start server:
   - Double-click START_SERVER.bat

5) Open from client computers:
   http://<server-pc-ip>:5000

6) Optional (auto-start on reboot):
   - Double-click INSTALL_SERVICE.bat (Run as Administrator)
   - Double-click INSTALL_BACKUP_TASK.bat (Run as Administrator)

Notes:
- Keep this folder on a dedicated server PC.
- Keep daily SQL backup copies in the backups folder.
- Default admin may be admin/admin on fresh database; change immediately.
