# GWS Homelab

.PHONY: help setup ping deploy stacks vault routeros health-sync

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Install Ansible + dependencies
	brew install ansible || true
	ansible-galaxy install -r requirements.yml
	# uptime-kuma-api is the Python dep for the lucasheld.uptime_kuma collection.
	# Must install into Ansible's own bundled Python (Homebrew isolates it from the system Python).
	`ansible --version | awk '/python version/{print $$NF}' | tr -d '()'` -m pip install --quiet --upgrade 'uptime-kuma-api>=1.2.0,<2.0.0'

ping: ## Test SSH to all hosts
	ansible all:!routers:!services -m ping; true

deploy: ## Apply full configuration
	ansible-playbook playbooks/site.yml

stacks: ## Deploy Docker Compose stacks to hosts
	ansible-playbook playbooks/deploy-stacks.yml

vault: ## Edit encrypted vault secrets
	ansible-vault edit inventory/group_vars/all/vault.yml

routeros: ## Configure MikroTik RouterOS
	ansible-playbook playbooks/configure-routeros.yml

health-sync: ## Refresh health DB on optiplex from an Apple Health export (preserves manual logs)
	ansible-playbook playbooks/sync-health-db.yml

test: ## Run unit tests for all apps
	@for dir in apps/*/; do \
		if [ -f "$$dir/pytest.ini" ]; then \
			echo "Running tests in $$dir..."; \
			(cd "$$dir" && python -m pytest) || exit 1; \
		fi; \
	done
