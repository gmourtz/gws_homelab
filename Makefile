# GWS Homelab

.PHONY: help setup ping bootstrap deploy stacks upgrade check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Install Ansible + dependencies
	brew install ansible || true
	ansible-galaxy install -r requirements.yml

ping: ## Test SSH to all hosts
	ansible all -m ping

bootstrap: ## First-time setup (run once after flash)
	ansible-playbook playbooks/bootstrap.yml

deploy: ## Apply full configuration
	ansible-playbook playbooks/site.yml

stacks: ## Deploy Docker Compose stacks to hosts
	ansible-playbook playbooks/deploy-stacks.yml

upgrade: ## Upgrade all packages
	ansible-playbook playbooks/site.yml --tags upgrade

check: ## Dry-run (no changes)
	ansible-playbook playbooks/site.yml --check --diff
