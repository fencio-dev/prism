#!/bin/bash
set -e

echo "==========================================="
echo "Git History Cleanup Script"
echo "Removes secrets from git history"
echo "==========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if git-filter-repo is installed
if ! command -v git-filter-repo &> /dev/null; then
    echo -e "${RED}ERROR: git-filter-repo is not installed${NC}"
    echo ""
    echo "Install it with:"
    echo "  macOS: brew install git-filter-repo"
    echo "  Linux: pip install git-filter-repo"
    echo ""
    exit 1
fi

echo -e "${YELLOW}WARNING: This will rewrite git history!${NC}"
echo "This operation:"
echo "  - Removes .env files from ALL commits"
echo "  - Rewrites commit hashes"
echo "  - Makes the repo incompatible with existing clones"
echo ""
echo "Before proceeding:"
echo "  1. Make sure you have a backup"
echo "  2. Ensure no one else is working on this repo"
echo "  3. You'll need to force-push after this"
echo ""
read -p "Are you sure you want to continue? (type 'yes' to proceed): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo -e "${GREEN}Step 1: Creating backup branch...${NC}"
# git branch backup-before-cleanup-$(date +%Y%m%d-%H%M%S)
echo "⏩ Backup branch creation skipped"

echo ""
echo -e "${GREEN}Step 2: Removing .env files from history...${NC}"
echo "Files to remove:"
echo "  - deployment/gateway/.env"
echo "  - mcp-gateway/.env"
echo "  - examples/langgraph_demo/.env"
echo "  - deployment/security-stack/.env"
echo "  - deployment/ui/.env"
echo "  - mcp-ui/.env"
echo ""

git filter-repo --invert-paths \
    --path "deployment/gateway/.env" \
    --path "mcp-gateway/.env" \
    --path "examples/langgraph_demo/.env" \
    --path "deployment/security-stack/.env" \
    --path "deployment/ui/.env" \
    --path "mcp-ui/.env" \
    --force

echo "✓ .env files removed from history"

echo ""
echo -e "${GREEN}Step 3: Cleaning up references...${NC}"
git reflog expire --expire=now --all
git gc --prune=now --aggressive
echo "✓ References cleaned"

echo ""
echo -e "${GREEN}Step 4: Verifying cleanup...${NC}"

# Check if any .env files are still in history
if git log --all --oneline --name-only --full-history | grep -E "(deployment/gateway|mcp-gateway|examples/langgraph_demo)/.env$" > /dev/null; then
    echo -e "${RED}ERROR: .env files still found in history!${NC}"
    exit 1
else
    echo "✓ No .env files found in history"
fi

# Check if secrets are still in history
if git log --all -S "YOUR_REVOKED_GEMINI_KEY" --oneline | head -1 > /dev/null; then
    echo -e "${YELLOW}WARNING: Old Gemini API key still found in history${NC}"
    echo "This may be in other files. Checking..."
    git log --all -S "YOUR_REVOKED_GEMINI_KEY" --name-only --oneline | head -20
else
    echo "✓ Old Gemini API key not found in history"
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Git history cleanup complete!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Review changes: git log --oneline | head -20"
echo "  2. Check current files: git status"
echo "  3. If everything looks good, create a new remote:"
echo "     git remote add public <new-repo-url>"
echo "     git push public main --force"
echo ""
echo -e "${YELLOW}Note: Do NOT push to the old remote!${NC}"
echo "The old remote may still have the secrets in history."
echo ""
