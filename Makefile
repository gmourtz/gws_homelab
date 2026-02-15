# ==============================================================
# GWS Homelab — Makefile
# ==============================================================

.PHONY: help setup ping bootstrap deploy upgrade lint check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Install Ansible and Galaxy dependencies
	brew install ansible || true
	ansible-galaxy install -r requirements.yml

download: ## Download OS images to ./images/
	./scripts/flash.sh download

build-iso: ## Build autoinstall ISO for OptiPlex
	./scripts/flash.sh build-iso

disks: ## List external disks (SD cards, USB sticks)
	./scripts/flash.sh list

ping: ## Test SSH connectivity to all hosts
	ansible all -m ping

bootstrap: ## First-time setup (user, SSH, sudo)
	ansible-playbook playbooks/bootstrap.yml

deploy: ## Apply full configuration to all hosts
	ansible-playbook playbooks/site.yml

upgrade: ## Upgrade all packages on all hosts
	ansible-playbook playbooks/site.yml --tags upgrade

lint: ## Lint all playbooks and roles
	ansible-lint playbooks/ roles/

check: ## Dry-run the site playbook (no changes)
	ansible-playbook playbooks/site.yml --check --diff

facts: ## Gather and display facts for all hosts
	ansible all -m setup --tree /tmp/facts
