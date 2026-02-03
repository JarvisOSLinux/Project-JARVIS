# JARVIS Arch Linux Package

## Building Locally

```bash
cd packaging/archlinux

# Build the package
makepkg -s

# Install it
makepkg -si

# Or install the built package directly
sudo pacman -U jarvis-ai-*.pkg.tar.zst
```

## For Development (editable install)

If you're developing JARVIS, use pip instead:

```bash
# Install in development mode
pip install -e .

# Install services manually
cp packaging/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

## Publishing to AUR

1. Update `pkgver` in PKGBUILD
2. Update `sha256sums` with actual checksum:
   ```bash
   updpkgsums
   ```
3. Regenerate .SRCINFO:
   ```bash
   makepkg --printsrcinfo > .SRCINFO
   ```
4. Push to AUR:
   ```bash
   git clone ssh://aur@aur.archlinux.org/jarvis-ai.git
   cp PKGBUILD .SRCINFO jarvis-ai/
   cd jarvis-ai
   git add -A
   git commit -m "Update to version X.Y.Z"
   git push
   ```

## Package Contents

After installation:

```
/usr/bin/jarvis                              # CLI entry point
/usr/lib/python3.x/site-packages/jarvis/     # Python package
/usr/lib/systemd/user/jarvis-daemon.service  # Daemon service
/usr/lib/systemd/user/jarvis-voice.service   # Voice service
/usr/share/doc/jarvis-ai/                    # Documentation
/usr/share/licenses/jarvis-ai/LICENSE        # License
```

## Usage After Install

```bash
# Start services
systemctl --user enable --now jarvis-daemon

# Use JARVIS
jarvis "what can you do?"

# Optional: Enable voice
systemctl --user enable --now jarvis-voice
```
