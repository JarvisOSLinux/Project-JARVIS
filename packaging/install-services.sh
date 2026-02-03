#!/bin/bash
# JARVIS Systemd Services Installer
# For Arch Linux and other systemd-based distributions

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "JARVIS Service Installer"
echo "========================"
echo ""

# Check if running as root (we don't want that for user services)
if [ "$EUID" -eq 0 ]; then
    echo "Error: Don't run this script as root."
    echo "User services should be installed as your normal user."
    exit 1
fi

# Create systemd user directory if it doesn't exist
mkdir -p "$SYSTEMD_USER_DIR"

# Copy service files
echo "Installing service files..."
cp "$SCRIPT_DIR/systemd/jarvis-daemon.service" "$SYSTEMD_USER_DIR/"
cp "$SCRIPT_DIR/systemd/jarvis-voice.service" "$SYSTEMD_USER_DIR/"

# Reload systemd
echo "Reloading systemd..."
systemctl --user daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "Quick start:"
echo "  systemctl --user start jarvis-daemon    # Start daemon"
echo "  systemctl --user enable jarvis-daemon   # Auto-start on login"
echo ""
echo "Optional voice service:"
echo "  systemctl --user start jarvis-voice     # Start voice"
echo "  systemctl --user enable jarvis-voice    # Auto-start on login"
echo ""
echo "Then use: jarvis \"your question\""
