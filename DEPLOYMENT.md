# Deployment Guide: Madaniyat Remote Server

The phone controller backend is deployed on a remote server accessible via the ssh alias `madaniyat`.

## Server Details
- **SSH Alias**: `madaniyat`
- **User**: `ubuntu`
- **Port**: `10555`
- **Directory**: `/home/ubuntu/phone_controller_backend`

## Systemd Service
The backend runs as a systemd service managed by the system:
- **Service Name**: `phone-controller-backend.service`
- **Service File**: `/etc/systemd/system/phone-controller-backend.service`

### The Symlink Trick
Because the service configuration points directly to `server.py` (which we refactored into a modular structure with `main.py` as the entrypoint), we bypass modifying the system-level systemd service file by using a symbolic link:
```bash
ln -s main.py ~/phone_controller_backend/server.py
```
This causes the service to load `main.py` whenever it starts/restarts the service.

## Managing the Service
If code changes are deployed via rsync, you can restart the backend simply by killing the current python process:
```bash
# Get the PID
ssh madaniyat "systemctl status phone-controller-backend.service"

# Kill the process (systemd will auto-restart it within 5 seconds)
ssh madaniyat "kill <PID>"
```
