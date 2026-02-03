# JARVIS Systemd Services

These are **user-level** systemd services for running JARVIS on Arch Linux (or any systemd-based distro).

## Installation

```bash
# Copy service files to user systemd directory
mkdir -p ~/.config/systemd/user/
cp jarvis-daemon.service ~/.config/systemd/user/
cp jarvis-voice.service ~/.config/systemd/user/

# Reload systemd
systemctl --user daemon-reload
```

## Usage

### Daemon Service (Core)

```bash
# Start the daemon
systemctl --user start jarvis-daemon

# Enable on login (auto-start)
systemctl --user enable jarvis-daemon

# Check status
systemctl --user status jarvis-daemon

# View logs
journalctl --user -u jarvis-daemon -f
```

### Voice Service (Optional)

```bash
# Start voice service (requires daemon to be running)
systemctl --user start jarvis-voice

# Enable on login
systemctl --user enable jarvis-voice

# Check status
systemctl --user status jarvis-voice

# View logs
journalctl --user -u jarvis-voice -f
```

## CLI Usage

Once the daemon is running as a service, use the CLI normally:

```bash
# Query JARVIS
jarvis "what time is it?"

# Check daemon status
jarvis daemon status

# Note: 'jarvis daemon start' is not needed when using systemd
```

## Architecture

```
┌─────────────────────────────────────────┐
│  systemd --user                         │
│  ├── jarvis-daemon.service (always on)  │
│  └── jarvis-voice.service (optional)    │
└─────────────────────────────────────────┘
              ▲
              │ socket :18789
              ▼
┌─────────────────────────────────────────┐
│  User commands                          │
│  ├── jarvis "query"                     │
│  ├── jarvis daemon status               │
│  └── KDE Widget (future)                │
└─────────────────────────────────────────┘
```

## Troubleshooting

### Daemon won't start
```bash
# Check logs
journalctl --user -u jarvis-daemon -n 50

# Check if Ollama is running
systemctl --user status ollama
# or
curl http://localhost:11434/api/tags
```

### Voice service can't access audio
```bash
# Make sure user is in audio group
groups $USER | grep audio

# If not, add user to audio group
sudo usermod -aG audio $USER
# Then logout and login again
```

### Port already in use
```bash
# Find what's using port 18789
ss -tlnp | grep 18789

# Kill existing process if needed
fuser -k 18789/tcp
```

## Uninstall

```bash
# Stop and disable services
systemctl --user stop jarvis-daemon jarvis-voice
systemctl --user disable jarvis-daemon jarvis-voice

# Remove service files
rm ~/.config/systemd/user/jarvis-daemon.service
rm ~/.config/systemd/user/jarvis-voice.service

# Reload systemd
systemctl --user daemon-reload
```
