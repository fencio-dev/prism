.PHONY: help install install-proxy test test-mgmt test-sdk clean run-mgmt run-data run-all run-mcp build-rust build-data lint format no-mcp generate-proto run-proxy stop-proxy

ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
LOG_DIR := $(ROOT)/data/logs
MCP_HEALTH_CHECK_TIMEOUT := 30
DATA_PLANE_HEALTH_CHECK_TIMEOUT := 30
PROXY_HEALTH_CHECK_TIMEOUT := 30
PRISM_PORT ?= 47000

ifneq (,$(filter no-mcp,$(MAKECMDGOALS)))
NO_MCP=1
endif

# Health check helpers
define wait_for_port
	@echo "⏳ Waiting for service on port $(1) (timeout: $(2)s)..."
	@for i in $$(seq 1 $(2)); do \
		if nc -z localhost $(1) 2>/dev/null; then \
			echo "✅ Service on port $(1) is ready"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "❌ Timeout waiting for port $(1) after $(2)s"; \
	exit 1
endef

define wait_for_mcp_server
	@echo "⏳ Waiting for MCP server on port 3001 (timeout: $(1)s)..."
	@for i in $$(seq 1 $(1)); do \
		if curl -s -H "Accept: text/event-stream" http://localhost:3001/mcp > /dev/null 2>&1; then \
			echo "✅ MCP server on port 3001 is ready"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "❌ Timeout waiting for MCP server after $(1)s"; \
	exit 1
endef

help:
	@echo "Semantic Security MVP - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install all dependencies (Python + Rust)"
	@echo "  make generate-proto   Generate Python gRPC stubs from protobuf"
	@echo "  make clean            Remove build artifacts and cache"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run all tests"
	@echo "  make test-mgmt        Run management-plane tests"
	@echo "  make test-sdk         Run Python SDK tests"
	@echo "  make test-rust        Run Rust tests"
	@echo ""
	@echo "Running:"
	@echo "  make run-mgmt         Run management-plane server (dev mode, port 47000, includes /mcp)"
	@echo "  make run-mgmt PORT=9000  Run with custom port"
	@echo "  make run-data         Run data-plane server (port 50051)"
	@echo "  make run-mcp          Run MCP server standalone (port 3001, dev only)"
	@echo "  make run-all          Run data-plane + management-plane (MCP embedded)"
	@echo ""
	@echo "Building:"
	@echo "  make build-rust       Build Rust data-plane library"
	@echo "  make build-data       Build data-plane (bridge-server)"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             Run linters (when configured)"
	@echo "  make format           Format code (when configured)"
	@echo ""
	@echo "Note: gRPC stubs are auto-generated when running services (run-mgmt, run-mcp, run-all)"

install:
	@echo "Installing Python dependencies..."
	uv sync --all-packages
	@echo "Building Rust component..."
	cd data_plane/tupl_dp/bridge && cargo build --release
	@echo "Building data-plane..."
	cd data_plane/tupl_dp/bridge && cargo build --release
	@$(MAKE) install-proxy
	@echo "✅ Setup complete!"

install-proxy:
	@echo "Installing fencio-proxy..."
	@if [ -d "$(HOME)/.prism/proxy" ]; then \
		echo "Updating proxy source..."; \
		git -C $(HOME)/.prism/proxy pull --ff-only origin main; \
	else \
		echo "Cloning proxy source..."; \
		git clone https://github.com/fencio-dev/proxy $(HOME)/.prism/proxy; \
	fi
	@echo "Building fencio-proxy binary..."
	mkdir -p $(HOME)/.prism/bin
	cd $(HOME)/.prism/proxy && go build -o $(HOME)/.prism/bin/fencio-proxy .
	@echo "✅ fencio-proxy installed!"

test:
	@echo "Running all tests..."
	uv run pytest management_plane/tests/ -v
	cd data_plane/tupl_dp/bridge && cargo test

test-mgmt:
	@echo "Running management-plane tests..."
	cd management_plane && uv run pytest tests/ -v

test-sdk:
	@echo "Running Python SDK tests..."
	@echo "No standalone SDK tests found in this repository"

test-rust:
	@echo "Running Rust tests..."
	cd data_plane/tupl_dp/bridge && cargo test

clean:
	@echo "Cleaning build artifacts..."
	rm -rf .venv
	rm -rf **/__pycache__
	rm -rf **/.pytest_cache
	rm -rf data_plane/tupl_dp/bridge/target
	rm -rf **/*.egg-info
	rm -rf .uv
	rm -f uv.lock
	rm -rf management_plane/app/generated
	@echo "✅ Cleaned!"

generate-proto:
	@echo "🔧 Generating Python gRPC stubs from protobuf..."
	@mkdir -p management_plane/app/generated
	@uv run python -m grpc_tools.protoc \
		-I data_plane/proto \
		--python_out=management_plane/app/generated \
		--grpc_python_out=management_plane/app/generated \
		data_plane/proto/rule_installation.proto
	@# Fix the import in the grpc file to use relative import
	@sed -i.bak 's/^import rule_installation_pb2/from . import rule_installation_pb2/' management_plane/app/generated/rule_installation_pb2_grpc.py
	@rm -f management_plane/app/generated/rule_installation_pb2_grpc.py.bak
	@echo "# Generated gRPC code from protobuf definitions" > management_plane/app/generated/__init__.py
	@echo "from .rule_installation_pb2 import *" >> management_plane/app/generated/__init__.py
	@echo "from .rule_installation_pb2_grpc import *" >> management_plane/app/generated/__init__.py
	@echo "✅ gRPC stubs generated!"

run-mgmt: generate-proto
	@echo "🚀 Starting management-plane server on port $(or $(PRISM_PORT),$(PORT),47000)..."
	@mkdir -p $(LOG_DIR)
	cd management_plane && PRISM_PORT=$(or $(PRISM_PORT),$(PORT),47000) DATA_PLANE_PORT=$(or $(DATA_PLANE_PORT),50051) uv run uvicorn app.main:app --host 0.0.0.0 --port $(or $(PRISM_PORT),$(PORT),47000)

run-data:
	@echo "🚀 Starting data-plane server on port 50051..."
	@mkdir -p $(LOG_DIR)
	mkdir -p data && cd data_plane/tupl_dp/bridge && HITLOG_SQLITE_PATH=$(PWD)/data/hitlogs.db cargo run --bin bridge-server

run-mcp: generate-proto
	@echo "🚀 Starting MCP server on port 3001..."
	@mkdir -p $(LOG_DIR)
	cd management_plane && uv run python -m mcp_server

run-all: generate-proto
	@echo "🚀 Starting all services..."
	@echo "   - Data Plane:       port $(or $(DATA_PLANE_PORT),50051)"
	@echo "   - Management Plane: port $(or $(PRISM_PORT),47000)  (includes /mcp)"
	@echo "   - Proxy:            port 47100"
	@echo ""
	@echo "📝 Logs will be written to:"
	@echo "   - Data Plane:       $(LOG_DIR)/data-plane.log"
	@echo "   - Management Plane: $(LOG_DIR)/management-plane.log"
	@echo "   - Proxy:            $(LOG_DIR)/proxy.log"
	@echo ""
	@mkdir -p $(LOG_DIR) data/pids
	@trap 'echo "🛑 Shutting down all services..."; kill 0' EXIT; \
	echo "📍 Step 1/3: Starting data-plane on port $(or $(DATA_PLANE_PORT),50051)..."; \
	(mkdir -p data && cd data_plane/tupl_dp/bridge && DATA_PLANE_PORT=$(or $(DATA_PLANE_PORT),50051) HITLOG_SQLITE_PATH=$(PWD)/data/hitlogs.db MANAGEMENT_PLANE_URL=http://localhost:$(or $(PRISM_PORT),47000)/api/v2 cargo run --bin bridge-server > $(LOG_DIR)/data-plane.log 2>&1) & \
	DATA_PLANE_PID=$$!; \
	echo "⏳ Waiting for data-plane on port $(or $(DATA_PLANE_PORT),50051) (timeout: $(DATA_PLANE_HEALTH_CHECK_TIMEOUT)s)..."; \
	for i in $$(seq 1 $(DATA_PLANE_HEALTH_CHECK_TIMEOUT)); do \
		if nc -z localhost $(or $(DATA_PLANE_PORT),50051) 2>/dev/null; then \
			echo "✅ Data-plane on port $(or $(DATA_PLANE_PORT),50051) is ready"; \
			break; \
		fi; \
		sleep 1; \
	done; \
	echo ""; \
	echo "📍 Step 2/3: Starting management-plane on port $(or $(PRISM_PORT),47000)..."; \
	(cd management_plane && PRISM_PORT=$(or $(PRISM_PORT),47000) DATA_PLANE_PORT=$(or $(DATA_PLANE_PORT),50051) uv run uvicorn app.main:app --host 0.0.0.0 --port $(or $(PRISM_PORT),47000) >> $(LOG_DIR)/management-plane.log 2>&1) & \
	MGMT_PID=$$!; \
	echo "⏳ Waiting for management-plane on port $(or $(PRISM_PORT),47000) (timeout: $(MCP_HEALTH_CHECK_TIMEOUT)s)..."; \
	for i in $$(seq 1 $(MCP_HEALTH_CHECK_TIMEOUT)); do \
		if nc -z localhost $(or $(PRISM_PORT),47000) 2>/dev/null; then \
			echo "✅ Management-plane on port $(or $(PRISM_PORT),47000) is ready"; \
			break; \
		fi; \
		sleep 1; \
	done; \
	echo ""; \
	echo "📍 Step 3/3: Starting fencio-proxy on port 47100..."; \
	(FENCIO_LISTEN_ADDR=:47100 FENCIO_API_ADDR=:47101 FENCIO_DB_TYPE=sqlite FENCIO_PRISM_URL=http://localhost:$(or $(PRISM_PORT),47000) FENCIO_ENFORCE_ENABLED=true ~/.prism/bin/fencio-proxy >> $(LOG_DIR)/proxy.log 2>&1) & \
	PROXY_PID=$$!; \
	echo $$PROXY_PID > data/pids/proxy.pid; \
	echo "⏳ Waiting for fencio-proxy on port 47100 (timeout: $(PROXY_HEALTH_CHECK_TIMEOUT)s)..."; \
	for i in $$(seq 1 $(PROXY_HEALTH_CHECK_TIMEOUT)); do \
		if nc -z localhost 47100 2>/dev/null; then \
			echo "✅ fencio-proxy on port 47100 is ready"; \
			break; \
		fi; \
		sleep 1; \
	done; \
	echo ""; \
	echo "✅ All services started! Press Ctrl+C to stop."; \
	echo ""; \
	wait

run-proxy:
	@echo "🚀 Starting fencio-proxy on port 47100..."
	@mkdir -p $(LOG_DIR) data/pids
	@FENCIO_LISTEN_ADDR=:47100 \
		FENCIO_API_ADDR=:47101 \
		FENCIO_DB_TYPE=sqlite \
		FENCIO_PRISM_URL=http://localhost:$(PRISM_PORT) \
		FENCIO_ENFORCE_ENABLED=true \
		~/.prism/bin/fencio-proxy >> $(LOG_DIR)/proxy.log 2>&1 & \
	echo $$! > data/pids/proxy.pid; \
	echo "✅ fencio-proxy started (PID $$(cat data/pids/proxy.pid))"

stop-proxy:
	@if [ -f data/pids/proxy.pid ]; then \
		kill $$(cat data/pids/proxy.pid) 2>/dev/null && echo "✅ fencio-proxy stopped" || echo "⚠  fencio-proxy was not running"; \
		rm -f data/pids/proxy.pid; \
	else \
		echo "⚠  No proxy PID file found (data/pids/proxy.pid)"; \
	fi

build-rust:
	@echo "Building Rust data-plane library..."
	cd data_plane/tupl_dp/bridge && cargo build --release
	@echo "✅ Built: data_plane/tupl_dp/bridge/target/release"

build-data:
	@echo "Building data-plane (bridge-server)..."
	cd data_plane/tupl_dp/bridge && cargo build --release
	@echo "✅ Built: data_plane/tupl_dp/bridge/target/release/bridge-server"

lint:
	@echo "Linting (ruff not configured yet)..."
	# uv run ruff check .

format:
	@echo "Formatting (ruff not configured yet)..."
	# uv run ruff format .

# Convenience aliases
t: test
tm: test-mgmt
ts: test-sdk
tr: test-rust
i: install
c: clean
r: run-mgmt
rd: run-data
ra: run-all

no-mcp:
	@:
