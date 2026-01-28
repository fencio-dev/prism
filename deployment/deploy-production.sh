#!/bin/bash
# MCP Gateway - Production Deployment Script
# Purpose: Deploy/update the MCP Gateway on existing EC2 instance
# Prerequisites: Docker, Docker Compose, Nginx, Certbot already installed
# Run from: deployment/gateway/ directory
# Usage: sudo bash deploy-production.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Detect docker-compose command
if docker compose version &>/dev/null; then
  DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
  DOCKER_COMPOSE="docker-compose"
else
  echo "❌ Error: Neither 'docker compose' nor 'docker-compose' found"
  echo "Please install Docker Compose: https://docs.docker.com/compose/install/"
  exit 1
fi

echo "🚀 Deploying MCP Gateway (Production)"
echo "Using: $DOCKER_COMPOSE"
echo "================================================"

# Check if .env file exists
if [ ! -f ".env" ]; then
  echo "❌ Error: .env file not found"
  echo "Please create .env from .env.production template and add your credentials"
  exit 1
fi

# Source environment variables from gateway
set -a
source .env
set +a

# Source environment variables from Console if available
CONSOLE_ENV_FILE="../console/.env.production"
if [ -f "$CONSOLE_ENV_FILE" ]; then
  echo "📄 Loading Console environment variables from $CONSOLE_ENV_FILE..."
  set -a
  source "$CONSOLE_ENV_FILE"
  set +a
  echo "✅ Console environment variables loaded"
else
  echo "⚠️ Warning: $CONSOLE_ENV_FILE not found. Console build may fail or be misconfigured."
  echo "   Expected path: $(realpath "$CONSOLE_ENV_FILE" 2>/dev/null || echo "$CONSOLE_ENV_FILE")"
fi

# Validate required environment variables
REQUIRED_VARS=(
  "GEMINI_API_KEY"
  "SUPABASE_URL"
  "SUPABASE_SERVICE_KEY"
  "SUPABASE_JWT_SECRET"
)

# VITE variables for UI build (will warn if missing but not fail)
VITE_VARS=(
  "VITE_API_BASE_URL"
  "VITE_GATEWAY_BASE_URL"
  "VITE_CONTROL_PLANE_URL"
  "VITE_SUPABASE_URL"
  "VITE_SUPABASE_ANON_KEY"
)

echo "🔍 Validating environment variables..."

# Check required variables
for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var}" ]; then
    echo "❌ Error: $var is not set in .env files"
    exit 1
  fi
done

# Check VITE variables (warn but don't fail)
MISSING_VITE=0
for var in "${VITE_VARS[@]}"; do
  if [ -z "${!var}" ]; then
    echo "⚠️  Warning: $var is not set (UI may not work correctly)"
    MISSING_VITE=1
  fi
done

if [ $MISSING_VITE -eq 0 ]; then
  echo "✅ All environment variables validated"
else
  echo "⚠️  Some VITE_* variables are missing - Console functionality may be limited"
  echo "   Check that ../console/.env.production exists and contains all VITE_* variables"
fi


# Build and start Docker containers
echo "🐳 Starting Docker containers..."
# Optional: Clean up docker system to free space before build
if [ "${DOCKER_PRUNE:-false}" = "true" ]; then
  echo "🧹 Running docker system prune..."
  docker system prune -f
else
  echo "ℹ️  Skipping docker prune (set DOCKER_PRUNE=true to enable)"
fi
$DOCKER_COMPOSE -f docker-compose.production.yml down || true
$DOCKER_COMPOSE -f docker-compose.production.yml up -d --build

# Wait for all services health check
echo "⏳ Waiting for services to become healthy..."
for i in {1..120}; do
  SECURITY_STACK_HEALTHY=false
  CONSOLE_HEALTHY=false

  if curl -f http://localhost:8001/health >/dev/null 2>&1; then
    SECURITY_STACK_HEALTHY=true
  fi

  if curl -f -s http://localhost:8080/ >/dev/null 2>&1; then
    CONSOLE_HEALTHY=true
  fi

  if [ "$SECURITY_STACK_HEALTHY" = true ] && [ "$CONSOLE_HEALTHY" = true ]; then
    echo "✅ All services are healthy!"
    break
  fi

  if [ $i -eq 120 ]; then
    echo "❌ Services failed to become healthy within 240 seconds (4 minutes)"
    echo "Security Stack: $SECURITY_STACK_HEALTHY, Console: $CONSOLE_HEALTHY"
    echo "Check logs with: $DOCKER_COMPOSE -f docker-compose.production.yml logs"
    exit 1
  fi

  # Show progress every 10 iterations
  if [ $((i % 10)) -eq 0 ]; then
    echo "   Still waiting... (Security: $SECURITY_STACK_HEALTHY, Console: $CONSOLE_HEALTHY) - ${i}s elapsed"
  fi

  sleep 2
done

# Configure Nginx
echo "🌐 Configuring Nginx..."
cp nginx.console.conf /etc/nginx/sites-available/guard.fencio.dev
ln -sf /etc/nginx/sites-available/guard.fencio.dev /etc/nginx/sites-enabled/

# Remove default nginx site if exists
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
nginx -t

# Reload Nginx
systemctl reload nginx
echo "✅ Nginx configured and reloaded"

# Setup SSL certificate with Let's Encrypt (first time only)
if [ ! -f /etc/letsencrypt/live/guard.fencio.dev/fullchain.pem ]; then
  echo "🔒 Obtaining SSL certificate from Let's Encrypt..."

  # Prompt for email if not set
  if [ -z "$LETSENCRYPT_EMAIL" ]; then
    read -p "Enter email for Let's Encrypt notifications: " LETSENCRYPT_EMAIL
  fi

  certbot --nginx \
    -d guard.fencio.dev \
    --non-interactive \
    --agree-tos \
    -m "$LETSENCRYPT_EMAIL" \
    --redirect

  echo "✅ SSL certificate obtained successfully"
else
  echo "✅ SSL certificate already exists"
fi

# Show service status
echo ""
echo "================================================"
echo "✅ Deployment complete!"
echo "================================================"
echo ""
echo "Service Status:"
$DOCKER_COMPOSE -f docker-compose.production.yml ps
echo ""
echo "🌐 Service URLs:"
echo "  Console: https://guard.fencio.dev"
echo "  Management Plane API: https://guard.fencio.dev/api/v1"
echo "  Telemetry API: https://guard.fencio.dev/api/v1/telemetry/sessions"
echo "  Health check: https://guard.fencio.dev/health"
echo ""
echo "Useful commands:"
echo "  View logs: $DOCKER_COMPOSE -f docker-compose.production.yml logs -f"
echo "  Stop service: $DOCKER_COMPOSE -f docker-compose.production.yml down"
echo "  Restart: sudo bash deploy-production.sh"
echo ""
echo "================================================"
