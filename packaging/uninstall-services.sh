#!/bin/bash
# JARVIS Systemd Services Uninstaller

set -e

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "JARVIS Service Uninstaller"
echo "=========================="
echo ""

# Stop services if running
echo "Stopping services..."
systemctl --user stop jarvis-voice 2>/dev/null || true
systemctl --user stop jarvis-daemon 2>/dev/null || true

# Disable services
echo "Disabling services..."
systemctl --user disable jarvis-voice 2>/dev/null || true
systemctl --user disable jarvis-daemon 2>/dev/null || true

# Remove service files
echo "Removing service files..."
rm -f "$SYSTEMD_USER_DIR/jarvis-daemon.service"
rm -f "$SYSTEMD_USER_DIR/jarvis-voice.service"

# Reload systemd
echo "Reloading systemd..."
systemctl --user daemon-reload

echo ""
echo "Uninstallation complete!"
