# fanctl Makefile
# https://github.com/process1183/rpi-fanctl
#
# Copyright (C) 2024  Josh Gadeken
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# The user that the fanctl.py script will run as. (Should not be root).
DAEMON_USER := $(shell whoami)

BASE_DIR := $(realpath $(CURDIR))
BIN_SRC := $(BASE_DIR)/fanctl.py
CONFIG_SRC := $(BASE_DIR)/fanctl.conf
SERVICE_SRC := $(BASE_DIR)/fanctl.service

# If you modify any of the *_DST vars, you may also need to update
# the corresponding paths in other fanctl files.
INSTALL_BASE := /opt/fanctl
BIN_DST := /opt/fanctl/$(notdir $(BIN_SRC))
CONFIG_DST := /opt/fanctl/$(notdir $(CONFIG_SRC))
SERVICE_DST := /etc/systemd/system/fanctl@$(strip $(DAEMON_USER)).service


.PHONY: install
install:
	sudo mkdir -p $(INSTALL_BASE)
	sudo cp $(BIN_SRC) $(BIN_DST)
	sudo cp $(CONFIG_SRC) $(CONFIG_DST)
	sudo cp $(SERVICE_SRC) $(SERVICE_DST)
	sudo chown $(strip $(DAEMON_USER)):$(strip $(DAEMON_USER)) $(BIN_DST) $(CONFIG_DST)
	sudo chmod 0500 $(BIN_DST)
	sudo chmod 0600 $(CONFIG_DST)
	sudo systemctl --system daemon-reload
	sudo systemctl enable $(notdir $(SERVICE_DST))
	@echo 'Systemd unit file installed. Start the fanctl daemon with: "sudo systemctl start $(notdir $(SERVICE_DST))"'


.PHONY: uninstall
uninstall:
	sudo systemctl stop $(notdir $(SERVICE_DST))
	sudo systemctl disable $(notdir $(SERVICE_DST))
	sudo rm -f $(BIN_DST) $(CONFIG_DST) $(SERVICE_DST)
	sudo rmdir $(INSTALL_BASE)
	sudo systemctl --system daemon-reload
