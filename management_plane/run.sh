#!/bin/bash
# Management Plane startup script

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting Management Plane...${NC}"

# Check if we're in the management_plane directory
if [ ! -f "pyproject.toml" ]; then
    echo -e "${RED}Error: pyproject.toml not found. Run this script from the management_plane directory.${NC}"
    exit 1
fi

# Check if Rust library exists
RUST_LIB="../data_plane/semantic-sandbox/target/release/libsemantic_sandbox.dylib"
if [ ! -f "$RUST_LIB" ]; then
    echo -e "${YELLOW}Warning: Rust library not found at $RUST_LIB${NC}"
    echo -e "${YELLOW}Building Rust library...${NC}"
    cd ../data_plane/semantic-sandbox
    cargo build --release
    cd ../management_plane
    echo -e "${GREEN}Rust library built successfully${NC}"
fi

# Install dependencies with uv if not already installed
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating virtual environment with uv...${NC}"
    uv venv
    echo -e "${GREEN}Virtual environment created${NC}"
fi

echo -e "${YELLOW}Syncing dependencies...${NC}"
uv sync

# Start the server with uvicorn
echo -e "${GREEN}Starting FastAPI server...${NC}"
echo -e "${YELLOW}API will be available at: http://localhost:8001${NC}"
echo -e "${YELLOW}Interactive docs at: http://localhost:8001/docs${NC}"
echo ""

uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level info
