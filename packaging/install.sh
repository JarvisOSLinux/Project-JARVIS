#!/bin/bash
# JARVIS AI Voice Assistant Installation Script
# For Linux Distribution Integration

set -e

# Configuration
JARVIS_USER="jarvis"
JARVIS_GROUP="jarvis"
INSTALL_DIR="/usr/lib/jarvis"
CONFIG_DIR="/etc/jarvis"
DATA_DIR="/var/lib/jarvis"
LOG_DIR="/var/log/jarvis"
BIN_DIR="/usr/bin"
SYSTEMD_DIR="/usr/lib/systemd/system"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

create_user() {
    log_info "Creating JARVIS system user..."
    
    # Create group if it doesn't exist
    if ! getent group "$JARVIS_GROUP" >/dev/null 2>&1; then
        groupadd -r "$JARVIS_GROUP"
        log_info "Created group: $JARVIS_GROUP"
    fi
    
    # Create user if it doesn't exist
    if ! getent passwd "$JARVIS_USER" >/dev/null 2>&1; then
        useradd -r -g "$JARVIS_GROUP" -d "$DATA_DIR" -s /sbin/nologin \
                -c "JARVIS AI Assistant" "$JARVIS_USER"
        log_info "Created user: $JARVIS_USER"
    fi
}

create_directories() {
    log_info "Creating directories..."
    
    # Create main directories
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$CONFIG_DIR"
    mkdir -p "$DATA_DIR"/models/piper
    mkdir -p "$LOG_DIR"
    
    # Set permissions
    chown -R "$JARVIS_USER:$JARVIS_GROUP" "$DATA_DIR"
    chown -R "$JARVIS_USER:$JARVIS_GROUP" "$LOG_DIR"
    chmod 755 "$DATA_DIR"
    chmod 755 "$LOG_DIR"
    
    log_info "Directories created and permissions set"
}

install_files() {
    log_info "Installing JARVIS files..."
    
    # Copy application files
    cp -r jarvis/* "$INSTALL_DIR/"
    
    # Install daemon script
    cp packaging/jarvis-daemon "$BIN_DIR/"
    chmod +x "$BIN_DIR/jarvis-daemon"
    
    # Install systemd service
    cp packaging/jarvis.service "$SYSTEMD_DIR/"
    
    # Install configuration template
    cp jarvis/config.env.template "$CONFIG_DIR/jarvis.conf.template"
    
    # Set ownership
    chown -R "$JARVIS_USER:$JARVIS_GROUP" "$INSTALL_DIR"
    chown "$JARVIS_USER:$JARVIS_GROUP" "$BIN_DIR/jarvis-daemon"
    chown "$JARVIS_USER:$JARVIS_GROUP" "$CONFIG_DIR/jarvis.conf.template"
    
    log_info "Files installed successfully"
}

install_dependencies() {
    log_info "Installing system dependencies..."
    
    # Detect package manager
    if command -v apt-get >/dev/null 2>&1; then
        # Debian/Ubuntu
        apt-get update
        apt-get install -y python3 python3-pip python3-venv python3-dev \
                          build-essential portaudio19-dev python3-pyaudio \
                          alsa-utils pulseaudio pulseaudio-utils \
                          systemd
    elif command -v yum >/dev/null 2>&1; then
        # RHEL/CentOS/Fedora
        yum install -y python3 python3-pip python3-devel gcc gcc-c++ \
                      portaudio-devel python3-pyaudio \
                      alsa-utils pulseaudio \
                      systemd
    elif command -v dnf >/dev/null 2>&1; then
        # Fedora
        dnf install -y python3 python3-pip python3-devel gcc gcc-c++ \
                      portaudio-devel python3-pyaudio \
                      alsa-utils pulseaudio \
                      systemd
    else
        log_warn "Unsupported package manager. Please install dependencies manually."
        log_warn "Required: python3, python3-pip, portaudio-dev, alsa-utils, systemd"
    fi
}

install_python_dependencies() {
    log_info "Installing Python dependencies..."
    
    # Create virtual environment for JARVIS
    python3 -m venv "$DATA_DIR/venv"
    source "$DATA_DIR/venv/bin/activate"
    
    # Upgrade pip
    pip install --upgrade pip
    
    # Install dependencies from requirements.txt
    pip install -r requirements.txt
    
    # Deactivate virtual environment
    deactivate
    
    # Set ownership of virtual environment
    chown -R "$JARVIS_USER:$JARVIS_GROUP" "$DATA_DIR/venv"
    
    log_info "Python dependencies installed"
}

setup_ollama() {
    log_info "Setting up Ollama..."
    
    # Check if Ollama is installed
    if ! command -v ollama >/dev/null 2>&1; then
        log_warn "Ollama not found. Please install Ollama manually:"
        log_warn "curl -fsSL https://ollama.com/install.sh | sh"
        log_warn "Then run: ollama pull codegemma:7b-instruct-q5_K_M"
    else
        log_info "Ollama found. Please ensure the model is available:"
        log_info "ollama pull codegemma:7b-instruct-q5_K_M"
    fi
}

setup_models() {
    log_info "Setting up TTS models..."
    
    if [ ! -f "$DATA_DIR/models/piper/en_US-libritts_r-medium.onnx" ]; then
        log_warn "TTS model not found. Please download it manually:"
        log_warn "wget -O $DATA_DIR/models/piper/en_US-libritts_r-medium.onnx \\"
        log_warn "  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx"
        log_warn "wget -O $DATA_DIR/models/piper/en_US-libritts_r-medium.onnx.json \\"
        log_warn "  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx.json"
    fi
    
    # Set ownership
    chown -R "$JARVIS_USER:$JARVIS_GROUP" "$DATA_DIR/models"
}

configure_service() {
    log_info "Configuring systemd service..."
    
    # Reload systemd
    systemctl daemon-reload
    
    # Enable service
    systemctl enable jarvis.service
    
    log_info "Service configured and enabled"
}

main() {
    log_info "Starting JARVIS AI Voice Assistant installation..."
    
    check_root
    create_user
    create_directories
    install_files
    install_dependencies
    install_python_dependencies
    setup_ollama
    setup_models
    configure_service
    
    log_info "Installation completed successfully!"
    log_info ""
    log_info "Next steps:"
    log_info "1. Download TTS models if not already present"
    log_info "2. Install Ollama and pull the required model"
    log_info "3. Configure JARVIS in $CONFIG_DIR/jarvis.conf"
    log_info "4. Start the service: systemctl start jarvis"
    log_info "5. Check status: systemctl status jarvis"
    log_info ""
    log_info "JARVIS is now ready for your Linux distribution!"
}

# Run main function
main "$@"
