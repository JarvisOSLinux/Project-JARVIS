# JARVIS AI Voice Assistant - Linux Distribution Packaging
# Makefile for building packages and managing installation

.PHONY: all clean install uninstall package-rpm package-deb test-service

# Configuration
PACKAGE_NAME = jarvis-ai
VERSION = 1.0.0
BUILD_DIR = build
DIST_DIR = dist

# Package managers
RPM_BUILD_DIR = $(BUILD_DIR)/rpm
DEB_BUILD_DIR = $(BUILD_DIR)/deb

all: clean package-rpm package-deb

clean:
	@echo "Cleaning build directories..."
	rm -rf $(BUILD_DIR)
	rm -rf $(DIST_DIR)
	rm -rf *.egg-info
	rm -rf dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# RPM Package Building
package-rpm: $(RPM_BUILD_DIR)
	@echo "Building RPM package..."
	mkdir -p $(RPM_BUILD_DIR)/SOURCES
	mkdir -p $(RPM_BUILD_DIR)/SPECS
	mkdir -p $(RPM_BUILD_DIR)/BUILD
	mkdir -p $(RPM_BUILD_DIR)/RPMS
	mkdir -p $(RPM_BUILD_DIR)/SRPMS
	
	# Create source tarball
	tar --exclude='.git*' \
	    --exclude='$(BUILD_DIR)' \
	    --exclude='$(DIST_DIR)' \
	    --exclude='venv' \
	    --exclude='*.pyc' \
	    --exclude='__pycache__' \
	    -czf $(RPM_BUILD_DIR)/SOURCES/$(PACKAGE_NAME)-$(VERSION).tar.gz .
	
	# Copy spec file
	cp packaging/jarvis.spec $(RPM_BUILD_DIR)/SPECS/
	
	# Build RPM
	rpmbuild --define "_topdir $(PWD)/$(RPM_BUILD_DIR)" \
	         --define "_version $(VERSION)" \
	         -ba $(RPM_BUILD_DIR)/SPECS/jarvis.spec
	
	@echo "RPM package built successfully!"
	@echo "Find packages in: $(RPM_BUILD_DIR)/RPMS/"

# DEB Package Building
package-deb: $(DEB_BUILD_DIR)
	@echo "Building DEB package..."
	mkdir -p $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)
	
	# Copy source files
	cp -r jarvis $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/
	cp -r packaging $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/
	cp -r models $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/
	cp requirements.txt $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/
	cp pyproject.toml $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/
	cp README.md $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/
	cp LICENSE $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/
	
	# Create debian directory
	mkdir -p $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian
	cp packaging/debian/control $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/
	
	# Create debian/rules
	echo '#!/usr/bin/make -f' > $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo 'export DH_VERBOSE=1' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo 'export DH_OPTIONS=--buildsystem=python3' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo 'override_dh_auto_install:' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tdh_auto_install' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tinstall -d debian/$(PACKAGE_NAME)/usr/lib/jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tinstall -d debian/$(PACKAGE_NAME)/etc/jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tinstall -d debian/$(PACKAGE_NAME)/var/lib/jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tinstall -d debian/$(PACKAGE_NAME)/var/log/jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tcp -r jarvis/* debian/$(PACKAGE_NAME)/usr/lib/jarvis/' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tinstall -m 755 packaging/jarvis-daemon debian/$(PACKAGE_NAME)/usr/bin/' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tinstall -m 644 packaging/jarvis.service debian/$(PACKAGE_NAME)/usr/lib/systemd/system/' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	echo '\tcp jarvis/config.env.template debian/$(PACKAGE_NAME)/etc/jarvis/jarvis.conf.template' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	chmod +x $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/rules
	
	# Create debian/changelog
	echo '$(PACKAGE_NAME) ($(VERSION)-1) unstable; urgency=low' > $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/changelog
	echo '' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/changelog
	echo '  * Initial package for JARVIS AI Voice Assistant' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/changelog
	echo '  * Systemd service integration' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/changelog
	echo '  * Complete Linux distribution packaging' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/changelog
	echo '' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/changelog
	echo ' -- YakupAtahanov <your.email@example.com>  $(shell date -R)' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/changelog
	
	# Create debian/postinst
	echo '#!/bin/sh' > $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo 'set -e' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo 'if [ "$1" = "configure" ]; then' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    # Create user and group' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    getent group jarvis >/dev/null || groupadd -r jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    getent passwd jarvis >/dev/null || useradd -r -g jarvis -d /var/lib/jarvis -s /sbin/nologin -c "JARVIS AI Assistant" jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    # Set permissions' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    chown -R jarvis:jarvis /var/lib/jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    chown -R jarvis:jarvis /var/log/jarvis' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    # Enable service' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    systemctl daemon-reload' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo '    systemctl enable jarvis.service' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	echo 'fi' >> $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	chmod +x $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION)/debian/postinst
	
	# Build DEB package
	cd $(DEB_BUILD_DIR)/$(PACKAGE_NAME)-$(VERSION) && dpkg-buildpackage -us -uc
	
	@echo "DEB package built successfully!"
	@echo "Find packages in: $(DEB_BUILD_DIR)/"

# Local installation (for testing)
install: clean
	@echo "Installing JARVIS locally for testing..."
	sudo bash packaging/install.sh

# Uninstall
uninstall:
	@echo "Uninstalling JARVIS..."
	systemctl stop jarvis.service || true
	systemctl disable jarvis.service || true
	rm -f /usr/lib/systemd/system/jarvis.service
	systemctl daemon-reload
	userdel jarvis || true
	groupdel jarvis || true
	rm -rf /usr/lib/jarvis
	rm -rf /etc/jarvis
	rm -rf /var/lib/jarvis
	rm -rf /var/log/jarvis
	rm -f /usr/bin/jarvis-daemon
	@echo "JARVIS uninstalled"

# Test service
test-service:
	@echo "Testing JARVIS service..."
	systemctl status jarvis.service
	journalctl -u jarvis.service --no-pager -l

# Help
help:
	@echo "Available targets:"
	@echo "  all          - Build all packages"
	@echo "  package-rpm  - Build RPM package"
	@echo "  package-deb  - Build DEB package"
	@echo "  install      - Install locally for testing"
	@echo "  uninstall    - Remove local installation"
	@echo "  test-service - Test systemd service"
	@echo "  clean        - Clean build directories"
	@echo "  help         - Show this help"
