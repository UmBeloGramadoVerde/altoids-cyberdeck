.PHONY: setup self-test display-benchmark input-latency-benchmark terminal-latency-benchmark commit-push stage reload rollback status update repair-runtime runtime-sync tmux-apply tmux-install-user tmux-install-system tmux-sync

RUNTIME_CTL ?= /opt/altoids/runtime/bin/altoidsctl
RUNTIME_BIN_DIR ?= /opt/altoids/runtime/bin
RELEASE_SOURCE ?= $(CURDIR)
SERVICE_NAME ?= altoids
SERVICE_USER ?= $(shell awk -F= '/^User=/{print $$2}' config/altoids.service | tail -n 1)
TMUX_CONF ?= $(CURDIR)/config/tmux.conf
TMUX_RUNTIME_CONF ?= /opt/altoids/runtime/tmux.conf

setup:
	./setup.sh

self-test:
	python3 -m altoids --self-test

display-benchmark:
	python3 scripts/display_benchmark.py

input-latency-benchmark:
	python3 scripts/input_latency_benchmark.py

terminal-latency-benchmark:
	python3 scripts/terminal_latency_benchmark.py

commit-push:
	./scripts/commit_push.sh "$(MSG)"

stage:
	sudo $(RUNTIME_CTL) stage $(RELEASE_SOURCE)

reload:
	sudo $(RUNTIME_CTL) reload

rollback:
	sudo $(RUNTIME_CTL) rollback

status:
	sudo $(RUNTIME_CTL) status

update:
	$(MAKE) runtime-sync
	$(MAKE) tmux-sync
	$(MAKE) self-test
	$(MAKE) stage
	$(MAKE) reload
	$(MAKE) status

repair-runtime:
	$(MAKE) runtime-sync
	sudo install -m 755 $(CURDIR)/config/altoids-runtime.py /opt/altoids/runtime/bin/altoids-runtime
	sudo chown -R $(SERVICE_USER):$(SERVICE_USER) /opt/altoids
	sudo systemctl restart $(SERVICE_NAME)
	sudo $(RUNTIME_CTL) status

runtime-sync:
	sudo install -d -m 755 $(RUNTIME_BIN_DIR)
	sudo install -m 755 $(CURDIR)/config/cdx $(RUNTIME_BIN_DIR)/cdx

tmux-apply:
	tmux start-server
	tmux source-file $(TMUX_RUNTIME_CONF)
	@echo "Reloaded tmux config from $(TMUX_RUNTIME_CONF)"

tmux-install-user:
	ln -sfn $(TMUX_RUNTIME_CONF) $(HOME)/.tmux.conf
	@echo "Linked tmux config at $(HOME)/.tmux.conf -> $(TMUX_RUNTIME_CONF)"

tmux-install-system:
	sudo ln -sfn $(TMUX_RUNTIME_CONF) /etc/tmux.conf
	@echo "Linked tmux config at /etc/tmux.conf -> $(TMUX_RUNTIME_CONF)"

tmux-sync:
	sudo install -d -m 755 $(dir $(TMUX_RUNTIME_CONF))
	sudo install -m 644 $(TMUX_CONF) $(TMUX_RUNTIME_CONF)
	$(MAKE) tmux-install-system
	$(MAKE) tmux-install-user
	$(MAKE) tmux-apply
