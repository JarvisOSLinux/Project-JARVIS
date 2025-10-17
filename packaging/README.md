# JARVIS AI Voice Assistant - Linux Distribution Integration

This directory contains all the necessary files and scripts to integrate JARVIS as a core component of your custom Linux distribution.

## 🎯 **Project Status: READY FOR LINUX DISTRIBUTION INTEGRATION**

✅ **Complete packaging system**  
✅ **Systemd service integration**  
✅ **Security-hardened configuration**  
✅ **Dedicated system user setup**  
✅ **Resource management**  
✅ **Cross-distribution support (RPM/DEB)**  

---

## 📦 **What's Included**

### **System Integration Files**
- `jarvis.service` - Systemd service configuration with security hardening
- `jarvis-daemon` - Production-ready daemon script with signal handling
- `install.sh` - Automated installation script for your distribution

### **Package Specifications**
- `jarvis.spec` - RPM package specification (RHEL/Fedora/CentOS)
- `debian/control` - DEB package specification (Debian/Ubuntu)
- `Makefile` - Build system for creating packages

### **Security Features**
- Dedicated `jarvis` system user with minimal privileges
- Sandboxed execution environment
- Resource limits and security restrictions
- Audit logging and monitoring

---

## 🚀 **Quick Start for Your Linux Distribution**

### **1. Build Packages**
```bash
# Build both RPM and DEB packages
make all

# Or build individually
make package-rpm    # For RHEL/Fedora/CentOS
make package-deb    # For Debian/Ubuntu
```

### **2. Install on Your Distribution**
```bash
# For RPM-based distributions
sudo rpm -ivh build/rpm/RPMS/*/jarvis-ai-*.rpm

# For DEB-based distributions  
sudo dpkg -i build/deb/jarvis-ai_*.deb

# Or use the automated installer
sudo ./packaging/install.sh
```

### **3. Start the Service**
```bash
sudo systemctl start jarvis
sudo systemctl enable jarvis  # Auto-start on boot
sudo systemctl status jarvis  # Check status
```

---

## 🔧 **Integration with Your Forked Kernel**

### **Kernel-Level Considerations**
Since you're building a custom OS based on a forked Linux kernel, consider these integrations:

1. **Audio Subsystem**: JARVIS requires ALSA/PulseAudio - ensure your kernel has proper audio drivers
2. **Input Devices**: Microphone support is essential
3. **Networking**: For LLM model downloads and updates
4. **Filesystem**: Ensure proper permissions for the `/var/lib/jarvis` directory

### **Init System Integration**
JARVIS is designed to work with systemd, which is the standard for most modern Linux distributions. If you're using a different init system, you'll need to adapt the service files.

---

## 🛡️ **Security Model**

### **System User**
- Dedicated `jarvis` user with minimal privileges
- No shell access (`/sbin/nologin`)
- Restricted home directory (`/var/lib/jarvis`)

### **Sandboxing**
- Systemd security restrictions
- No new privileges
- Private temporary directories
- Restricted system calls
- Memory protection

### **Resource Limits**
- File descriptor limits: 65,536
- Process limits: 4,096
- Memory protection enabled
- Real-time restrictions

---

## 📊 **Resource Requirements**

### **Minimum System Requirements**
- **RAM**: 8GB (16GB recommended)
- **Storage**: 10GB for models and data
- **CPU**: x86_64 with AVX2 support
- **Audio**: Microphone and speakers

### **Dependencies**
- Python 3.10+
- Ollama (for LLM models)
- Audio libraries (ALSA/PulseAudio)
- Systemd

---

## 🔄 **Service Management**

### **Basic Commands**
```bash
# Service control
sudo systemctl start jarvis
sudo systemctl stop jarvis
sudo systemctl restart jarvis
sudo systemctl reload jarvis

# Status and logs
sudo systemctl status jarvis
sudo journalctl -u jarvis -f
sudo journalctl -u jarvis --since "1 hour ago"
```

### **Configuration**
- Main config: `/etc/jarvis/jarvis.conf`
- Logs: `/var/log/jarvis/`
- Data: `/var/lib/jarvis/`
- Models: `/var/lib/jarvis/models/`

---

## 🏗️ **Building Your Custom OS**

### **1. Kernel Integration**
Your forked kernel should include:
- Audio drivers (ALSA support)
- Input device drivers (microphone)
- Network stack for model downloads
- Filesystem with proper permission handling

### **2. Distribution Packages**
Include JARVIS in your distribution's package repository:
```bash
# Add to your package repository
cp build/rpm/RPMS/*/jarvis-ai-*.rpm /path/to/your/repo/
createrepo /path/to/your/repo/
```

### **3. Default Configuration**
Consider pre-configuring JARVIS for your OS:
- Default model paths
- Optimized settings for your target hardware
- Custom MCP servers specific to your distribution

---

## 🔍 **Troubleshooting**

### **Common Issues**

**Service won't start:**
```bash
sudo journalctl -u jarvis --no-pager -l
# Check for missing dependencies or permission issues
```

**Audio not working:**
```bash
# Check audio devices
arecord -l
aplay -l
# Ensure jarvis user has audio group access
sudo usermod -a -G audio jarvis
```

**Models not found:**
```bash
# Download required models
sudo -u jarvis ollama pull codegemma:7b-instruct-q5_K_M
# Download TTS models to /var/lib/jarvis/models/piper/
```

### **Performance Tuning**
```bash
# Monitor resource usage
sudo systemctl status jarvis
htop -u jarvis

# Adjust systemd resource limits in jarvis.service if needed
```

---

## 🎉 **Your AI-Native OS is Ready!**

With these integration files, JARVIS is now ready to be a core component of your custom Linux distribution. The system provides:

- **Seamless integration** with systemd and package management
- **Enterprise-grade security** with proper sandboxing
- **Production-ready reliability** with error handling and logging
- **Scalable architecture** that can grow with your OS

Your vision of an AI-native operating system is not just feasible—it's ready to deploy! 🚀

---

## 📞 **Support**

For issues specific to Linux distribution integration:
1. Check the logs: `sudo journalctl -u jarvis -f`
2. Verify permissions: `ls -la /var/lib/jarvis /var/log/jarvis`
3. Test dependencies: `python3 -c "import jarvis"`
4. Review systemd status: `sudo systemctl status jarvis`

The foundation is solid. Your AI-powered OS awaits! 🤖✨
