#!/usr/bin/env bash
# ==============================================================================
# Edge Impulse Stack - Interactive Service Selector
# ==============================================================================
# Requires: gum (https://github.com/charmbracelet/gum)
# Fallback: basic bash select if gum not found
# ==============================================================================

set -euo pipefail

trap 'echo -e "\n${DIM:-}  Cancelled.${NC:-}"; exit 0' INT TERM

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Service definitions
declare -A SERVICE_DEPS=(
    [ei-runner]=""
)

# Reverse dependency map: what breaks if you stop X
declare -A SERVICE_DEPENDENTS=(
    [ei-runner]=""
)

ALL_SERVICES=(ei-runner)

# Get container status for a service
get_status() {
    local service="$1"
    local container_name="p4n4-${service}"
    local status
    status=$(docker inspect --format='{{.State.Status}}' "$container_name" 2>/dev/null || echo "not found")
    echo "$status"
}

# Get port mapping for a service
get_port() {
    local service="$1"
    case "$service" in
        ei-runner) echo "8080" ;;
    esac
}

# Print status table
print_status() {
    echo ""
    printf "${BOLD}  %-12s %-10s %-12s %s${NC}\n" "SERVICE" "STATUS" "PORTS" "DEPENDS ON"
    printf "  ${DIM}%-12s %-10s %-12s %s${NC}\n" "───────────" "────────" "──────────" "──────────"

    for service in "${ALL_SERVICES[@]}"; do
        local status
        status=$(get_status "$service")
        local port
        port=$(get_port "$service")
        local deps="${SERVICE_DEPS[$service]:-}"
        [ -z "$deps" ] && deps="-"

        local status_color
        case "$status" in
            running)   status_color="${GREEN}running${NC}" ;;
            exited)    status_color="${RED}stopped${NC}" ;;
            *)         status_color="${DIM}$status${NC}" ;;
        esac

        printf "  ${BOLD}%-12s${NC} %-19s %-12s %s\n" \
            "$service" "$status_color" "$port" "$deps"
    done
    echo ""
}

# Resolve dependencies: given a list of services, add required deps
resolve_deps() {
    local -a selected=("$@")
    local -a resolved=()
    local changed=true

    while $changed; do
        changed=false
        for service in "${selected[@]}"; do
            if [[ ! " ${resolved[*]} " =~ " $service " ]]; then
                resolved+=("$service")
            fi
            local deps="${SERVICE_DEPS[$service]}"
            if [ -n "$deps" ]; then
                IFS=',' read -ra dep_array <<< "$deps"
                for dep in "${dep_array[@]}"; do
                    if [[ ! " ${resolved[*]} " =~ " $dep " ]]; then
                        resolved+=("$dep")
                        changed=true
                    fi
                done
            fi
        done
        selected=("${resolved[@]}")
    done

    echo "${resolved[@]}"
}

# Check what will break if stopping a service
check_dependents() {
    local service="$1"
    local dependents="${SERVICE_DEPENDENTS[$service]}"
    if [ -n "$dependents" ]; then
        IFS=',' read -ra dep_array <<< "$dependents"
        local running_deps=()
        for dep in "${dep_array[@]}"; do
            local status
            status=$(get_status "$dep")
            if [ "$status" = "running" ]; then
                running_deps+=("$dep")
            fi
        done
        if [ ${#running_deps[@]} -gt 0 ]; then
            echo "${running_deps[*]}"
        fi
    fi
}

# Interactive selector using gum
select_with_gum() {
    echo ""
    printf "${BOLD}${CYAN}  Edge Impulse Stack - Interactive Service Selector${NC}\n"
    print_status

    local options=()
    local preselected=()

    for service in "${ALL_SERVICES[@]}"; do
        local status
        status=$(get_status "$service")
        local deps="${SERVICE_DEPS[$service]}"
        local label="$service"

        [ -n "$deps" ] && label="$label  <- deps: $deps"

        options+=("$label")

        if [ "$status" = "running" ]; then
            preselected+=("$label")
        fi
    done

    printf "${BOLD}  Select services to run (space to toggle, enter to confirm):${NC}\n\n"

    local selected
    if [ ${#preselected[@]} -gt 0 ]; then
        selected=$(gum choose --no-limit \
            --header="  Edge: ei-runner" \
            --cursor.foreground="6" \
            --selected.foreground="2" \
            --selected="${preselected[*]}" \
            "${options[@]}" 2>/dev/null || true)
    else
        selected=$(gum choose --no-limit \
            --header="  Edge: ei-runner" \
            --cursor.foreground="6" \
            --selected.foreground="2" \
            "${options[@]}" 2>/dev/null || true)
    fi

    if [ -z "$selected" ]; then
        echo -e "${YELLOW}  No services selected. Exiting.${NC}"
        exit 0
    fi

    local -a selected_services=()
    while IFS= read -r line; do
        local svc
        svc=$(echo "$line" | awk '{print $1}')
        selected_services+=("$svc")
    done <<< "$selected"

    apply_selection "${selected_services[@]}"
}

# Fallback selector without gum
select_without_gum() {
    echo ""
    printf "${BOLD}${CYAN}  Edge Impulse Stack - Service Selector${NC}\n"
    printf "${DIM}  (Install 'gum' for a better experience: https://github.com/charmbracelet/gum)${NC}\n"
    print_status

    echo -e "${BOLD}  Available actions:${NC}"
    echo -e "  ${GREEN}1${NC}) Start ALL services     (ei-runner)"
    echo -e "  ${GREEN}2${NC}) Stop ALL services"
    echo -e "  ${GREEN}3${NC}) Custom selection"
    echo -e "  ${GREEN}4${NC}) Exit"
    echo ""

    read -rp "  Select option [1-4]: " choice

    case "$choice" in
        1) apply_selection "${ALL_SERVICES[@]}" ;;
        2) stop_all ;;
        3) custom_selection ;;
        4) exit 0 ;;
        *) echo -e "${RED}  Invalid option${NC}"; exit 1 ;;
    esac
}

# Custom selection fallback
custom_selection() {
    echo ""
    echo -e "${BOLD}  Enter service names separated by spaces:${NC}"
    echo -e "${DIM}  Available: ${ALL_SERVICES[*]}${NC}"
    echo ""
    read -rp "  Services: " input

    if [ -z "$input" ]; then
        echo -e "${YELLOW}  No services selected.${NC}"
        exit 0
    fi

    local -a selected_services=()
    for svc in $input; do
        if [[ " ${ALL_SERVICES[*]} " =~ " $svc " ]]; then
            selected_services+=("$svc")
        else
            echo -e "${RED}  Unknown service: $svc${NC}"
        fi
    done

    if [ ${#selected_services[@]} -eq 0 ]; then
        echo -e "${RED}  No valid services selected.${NC}"
        exit 1
    fi

    apply_selection "${selected_services[@]}"
}

# Apply the selected services
apply_selection() {
    local -a selected=("$@")

    local -a resolved
    IFS=' ' read -ra resolved <<< "$(resolve_deps "${selected[@]}")"

    local -a auto_added=()
    for dep in "${resolved[@]}"; do
        if [[ ! " ${selected[*]} " =~ " $dep " ]]; then
            auto_added+=("$dep")
        fi
    done

    if [ ${#auto_added[@]} -gt 0 ]; then
        echo -e "${YELLOW}  Auto-adding dependencies: ${BOLD}${auto_added[*]}${NC}"
    fi

    local -a to_stop=()
    for service in "${ALL_SERVICES[@]}"; do
        local status
        status=$(get_status "$service")
        if [ "$status" = "running" ] && [[ ! " ${resolved[*]} " =~ " $service " ]]; then
            to_stop+=("$service")
        fi
    done

    if [ ${#to_stop[@]} -gt 0 ]; then
        echo ""
        echo -e "${YELLOW}  Services to stop: ${BOLD}${to_stop[*]}${NC}"

        for svc in "${to_stop[@]}"; do
            local affected
            affected=$(check_dependents "$svc")
            if [ -n "$affected" ]; then
                echo -e "${RED}  WARNING: Stopping '$svc' affects running services: ${BOLD}$affected${NC}"
            fi
        done

        echo ""
        read -rp "  Continue? [y/N]: " confirm
        if [[ ! "$confirm" =~ ^[yY]$ ]]; then
            echo -e "${DIM}  Cancelled.${NC}"
            exit 0
        fi

        echo -e "${DIM}  Stopping: ${to_stop[*]}...${NC}"
        if ! docker compose stop "${to_stop[@]}" 2>/dev/null; then
            echo -e "${RED}  ERROR: Failed to stop services.${NC}"
            exit 1
        fi
    fi

    local -a to_start=()
    for service in "${resolved[@]}"; do
        local status
        status=$(get_status "$service")
        if [ "$status" != "running" ]; then
            to_start+=("$service")
        fi
    done

    if [ ${#to_start[@]} -gt 0 ]; then
        echo -e "${GREEN}  Starting: ${BOLD}${to_start[*]}${NC}"
        if ! docker compose up -d "${to_start[@]}"; then
            echo -e "${RED}  ERROR: Failed to start services.${NC}"
            exit 1
        fi
    else
        echo -e "${DIM}  All selected services already running.${NC}"
    fi

    echo ""
    echo -e "${GREEN}${BOLD}  Done!${NC}"
    print_status
}

# Stop all services
stop_all() {
    echo -e "${YELLOW}  Stopping all services...${NC}"
    docker compose down
    echo -e "${GREEN}  All services stopped.${NC}"
}

# Main
main() {
    if [ ! -f "docker-compose.yml" ]; then
        echo -e "${RED}  Error: docker-compose.yml not found. Run from project root.${NC}"
        exit 1
    fi

    if ! command -v docker &>/dev/null; then
        echo -e "${RED}  Error: docker not found. Please install Docker.${NC}"
        exit 1
    fi

    if command -v gum &>/dev/null; then
        select_with_gum
    else
        select_without_gum
    fi
}

main "$@"
