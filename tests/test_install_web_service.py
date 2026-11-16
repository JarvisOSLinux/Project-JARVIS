"""scripts/install-web-service.sh installs tailscale itself when it is missing."""

import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-web-service.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!{shutil.which('bash')}\n{body}\n")
    path.chmod(0o755)


def test_missing_tailscale_is_installed_then_login_is_required(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    # A venv whose python has every module ensure_env asks for, so the test
    # never pip-installs into, or depends on, the interpreter running pytest.
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _stub(venv / "bin", "python", "exit 0")
    (bin_dir / "dirname").symlink_to(shutil.which("dirname") or "/usr/bin/dirname")
    _stub(bin_dir, "sudo", 'exec "$@"')
    _stub(bin_dir, "systemctl", f'echo "systemctl $*" >>{calls}; [ "$1" != is-active ]')
    # The installed tailscale is not logged in: `tailscale status` fails.
    _stub(
        bin_dir,
        "pacman",
        f'echo "pacman $*" >>{calls}\n'
        f"printf '#!/bin/sh\\nexit 1\\n' >{bin_dir}/tailscale\n"
        f"{shutil.which('chmod')} +x {bin_dir}/tailscale",
    )

    result = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(SCRIPT)],
        env={
            "PATH": str(bin_dir),
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "VENV": str(venv),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    log = calls.read_text() if calls.exists() else ""
    assert "pacman -S --needed --noconfirm tailscale" in log
    assert "systemctl enable --now tailscaled" in log
    assert result.returncode == 1
    assert "not logged in" in result.stderr
    assert not (tmp_path / "config" / "systemd").exists()
