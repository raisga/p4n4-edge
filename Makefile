# ==============================================================================
# Edge Impulse Stack Makefile
# ==============================================================================

.PHONY: help up down restart logs ps clean pull build status interactive start stop \
        deploy-model test-inference

# Colors
GREEN  := \033[0;32m
YELLOW := \033[1;33m
CYAN   := \033[0;36m
RED    := \033[0;31m
BOLD   := \033[1m
DIM    := \033[2m
NC     := \033[0m

# Service list
EDGE_SERVICES := ei-runner

# Default target
help:
	@echo ""
	@printf "$(BOLD)$(CYAN)  Edge Impulse Stack$(NC) - Available Commands\n"
	@echo "  ════════════════════════════════════════════"
	@echo ""
	@printf "  $(BOLD)Core:$(NC)\n"
	@printf "    $(GREEN)make up$(NC)              Start all services\n"
	@printf "    $(GREEN)make down$(NC)            Stop all services\n"
	@printf "    $(GREEN)make restart$(NC)         Restart all services\n"
	@printf "    $(GREEN)make status$(NC)          Show service status table\n"
	@printf "    $(GREEN)make logs$(NC)            Follow logs for all services\n"
	@printf "    $(GREEN)make ps$(NC)              Docker compose ps\n"
	@printf "    $(GREEN)make pull$(NC)            Pull latest base images\n"
	@printf "    $(GREEN)make build$(NC)           Build the runner image\n"
	@printf "    $(GREEN)make clean$(NC)           Stop and remove volumes\n"
	@echo ""
	@printf "  $(BOLD)Modular:$(NC)\n"
	@printf "    $(GREEN)make interactive$(NC)     Interactive service selector\n"
	@printf "    $(GREEN)make start SERVICE=x$(NC) Start a service + its deps\n"
	@printf "    $(GREEN)make stop SERVICE=x$(NC)  Stop a service (warns about deps)\n"
	@echo ""
	@printf "  $(BOLD)Edge Impulse:$(NC)\n"
	@printf "    $(GREEN)make deploy-model MODEL=path/to/model.eim$(NC)\n"
	@printf "                         Copy a .eim model to edge-impulse/models/\n"
	@echo ""
	@printf "  $(BOLD)Testing:$(NC)\n"
	@printf "    $(GREEN)make test-inference$(NC)  Send test sensor data to MQTT\n"
	@echo ""

# ------------------------------------------------------------------------------
# Core Commands
# ------------------------------------------------------------------------------

up:
	@echo "Starting Edge Impulse stack..."
	@echo ""
	@printf "$(YELLOW)  NOTE: p4n4-net must exist. Start p4n4-iot first, or run:$(NC)\n"
	@printf "$(YELLOW)        docker network create p4n4-net$(NC)\n"
	@echo ""
	docker compose up -d
	@echo ""
	@echo "Services started! Access them at:"
	@printf "  $(CYAN)Runner health$(NC): http://localhost:8080/health\n"
	@echo ""

down:
	@echo "Stopping Edge Impulse stack..."
	docker compose down

restart:
	@echo "Restarting Edge Impulse stack..."
	docker compose restart

logs:
	docker compose logs -f

ps:
	docker compose ps

pull:
	@echo "Pulling latest base images..."
	docker compose pull

build:
	@echo "Building Edge Impulse runner image..."
	docker compose build

clean:
	@printf "$(RED)$(BOLD)  WARNING: This will DELETE ALL DATA (runner state, inference logs)$(NC)\n"
	@read -p "  Type 'yes' to confirm: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		echo "Stopping services and removing volumes..."; \
		docker compose down -v; \
		echo "Cleaned up!"; \
	else \
		echo "Cancelled."; \
	fi

# ------------------------------------------------------------------------------
# Status (colorized service table)
# ------------------------------------------------------------------------------

status:
	@echo ""
	@printf "$(BOLD)$(CYAN)  Edge Impulse Stack - Service Status$(NC)\n"
	@echo "  ════════════════════════════════════════════════════════════════════"
	@printf "  $(BOLD)%-14s %-12s %-8s %s$(NC)\n" "SERVICE" "STATUS" "PORT" "URL"
	@printf "  $(DIM)%-14s %-12s %-8s %s$(NC)\n" "─────────────" "──────────" "──────" "───────────────────────────"
	@for svc in ei-runner; do \
		container="p4n4-$$svc"; \
		state=$$(docker inspect --format='{{.State.Status}}' $$container 2>/dev/null || echo "stopped"); \
		case $$svc in \
			ei-runner) port="8080"; url="http://localhost:8080/health" ;; \
		esac; \
		if [ "$$state" = "running" ]; then \
			printf "  $(BOLD)%-14s$(NC) $(GREEN)%-12s$(NC) %-8s %s\n" "$$svc" "running" "$$port" "$$url"; \
		else \
			printf "  $(BOLD)%-14s$(NC) $(RED)%-12s$(NC) %-8s $(DIM)%s$(NC)\n" "$$svc" "$$state" "$$port" "-"; \
		fi; \
	done
	@echo ""

# ------------------------------------------------------------------------------
# Interactive Service Selector
# ------------------------------------------------------------------------------

interactive:
	@bash scripts/selector.sh

# ------------------------------------------------------------------------------
# Granular Start/Stop with Dependency Awareness
# ------------------------------------------------------------------------------

# Dependency map
deps_ei-runner :=

# Reverse deps (what breaks)
rdeps_ei-runner :=

start:
ifndef SERVICE
	@printf "$(RED)  Usage: make start SERVICE=<name>$(NC)\n"
	@printf "  Available: $(BOLD)ei-runner$(NC)\n"
	@exit 1
endif
	@deps="$(deps_$(SERVICE))"; \
	if [ -n "$$deps" ]; then \
		printf "$(YELLOW)  Auto-starting dependencies: $(BOLD)$$deps$(NC)\n"; \
		docker compose up -d $$deps; \
	fi
	@printf "$(GREEN)  Starting $(BOLD)$(SERVICE)$(NC)$(GREEN)...$(NC)\n"
	@docker compose up -d $(SERVICE)
	@printf "$(GREEN)$(BOLD)  Done!$(NC)\n"

stop:
ifndef SERVICE
	@printf "$(RED)  Usage: make stop SERVICE=<name>$(NC)\n"
	@printf "  Available: $(BOLD)ei-runner$(NC)\n"
	@exit 1
endif
	@rdeps="$(rdeps_$(SERVICE))"; \
	if [ -n "$$rdeps" ]; then \
		for dep in $$rdeps; do \
			state=$$(docker inspect --format='{{.State.Status}}' "p4n4-$$dep" 2>/dev/null || echo "stopped"); \
			if [ "$$state" = "running" ]; then \
				printf "$(RED)  WARNING: Stopping '$(SERVICE)' will affect running service: $(BOLD)$$dep$(NC)\n"; \
			fi; \
		done; \
	fi
	@printf "$(YELLOW)  Stopping $(BOLD)$(SERVICE)$(NC)$(YELLOW)...$(NC)\n"
	@docker compose stop $(SERVICE)
	@printf "$(GREEN)$(BOLD)  Done!$(NC)\n"

# ------------------------------------------------------------------------------
# Edge Impulse Model Deployment
# ------------------------------------------------------------------------------

deploy-model:
ifndef MODEL
	@printf "$(RED)  Usage: make deploy-model MODEL=path/to/model.eim$(NC)\n"
	@exit 1
endif
	@printf "$(CYAN)  Deploying model: $(BOLD)$(MODEL)$(NC)\n"
	@cp "$(MODEL)" edge-impulse/models/
	@printf "$(GREEN)  Model copied to edge-impulse/models/$(NC)\n"
	@printf "$(YELLOW)  Set EI_MODEL_FILE=$(notdir $(MODEL)) in your .env, then restart:$(NC)\n"
	@printf "$(YELLOW)    make restart$(NC)\n"

# ------------------------------------------------------------------------------
# Testing Commands
# ------------------------------------------------------------------------------

test-inference:
	@printf "$(CYAN)  Publishing test sensor data to MQTT (topic: sensors/raw)...$(NC)\n"
	@docker run --rm --network p4n4-net eclipse-mosquitto:2 \
		mosquitto_pub -h p4n4-mqtt -t 'sensors/raw' \
		-m '{"device":"test-sensor-01","values":[1.23,4.56,7.89,0.12,3.45,6.78]}' \
		2>/dev/null \
		|| printf "$(RED)  Could not connect to p4n4-mqtt. Is p4n4-iot running?$(NC)\n"
	@echo ""
	@printf "$(CYAN)  Subscribing to inference/results for 3 seconds...$(NC)\n"
	@docker run --rm --network p4n4-net eclipse-mosquitto:2 \
		mosquitto_sub -h p4n4-mqtt -t 'inference/results' -W 3 \
		2>/dev/null \
		|| printf "$(DIM)  No results received (check ei-runner logs: make logs)$(NC)\n"
