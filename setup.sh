#!/bin/bash
# ============================================================
#  VPS AI Stack — One-command setup script
#  Run as root or with sudo on a fresh Ubuntu 22.04+ VPS
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}"
echo "╔═══════════════════════════════════════╗"
echo "║        VPS AI Stack Setup             ║"
echo "║   Privacy-First AI for Your Business  ║"
echo "╚═══════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run as root or with sudo${NC}"
  exit 1
fi

# Install Docker if not present
if ! command -v docker &> /dev/null; then
  echo -e "${YELLOW}Installing Docker...${NC}"
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
  echo -e "${GREEN}Docker installed.${NC}"
else
  echo -e "${GREEN}Docker already installed.${NC}"
fi

# Install Docker Compose plugin if not present
if ! docker compose version &> /dev/null; then
  echo -e "${YELLOW}Installing Docker Compose...${NC}"
  apt-get update -qq
  apt-get install -y docker-compose-plugin
fi

# Create .env from example if not present
if [ ! -f .env ]; then
  cp .env.example .env
  echo -e "${YELLOW}"
  echo "──────────────────────────────────────────"
  echo "  ACTION REQUIRED: Edit your .env file"
  echo "  Set your domain, passwords, and email"
  echo "  Then run this script again."
  echo "──────────────────────────────────────────"
  echo -e "${NC}"
  exit 0
fi

# Validate required env vars
source .env
if [ "$DOMAIN" = "yourdomain.com" ]; then
  echo -e "${RED}Error: Please set your DOMAIN in .env before continuing.${NC}"
  exit 1
fi

if [ "$POSTGRES_PASSWORD" = "change_me_strong_password" ]; then
  echo -e "${RED}Error: Please change POSTGRES_PASSWORD in .env.${NC}"
  exit 1
fi

# Set permissions for Traefik certs
mkdir -p ./configs/traefik
touch ./configs/traefik/acme.json 2>/dev/null || true

echo -e "${YELLOW}Starting services...${NC}"
docker compose pull
docker compose up -d

echo ""
echo -e "${GREEN}✓ Stack is running!${NC}"
echo ""
echo "Your services will be available at:"
echo "  🤖 AI Chat:    https://chat.$DOMAIN"
echo "  ⚙️  Automation: https://automation.$DOMAIN"
echo ""
echo "SSL certificates are being issued automatically."
echo "This may take 1-2 minutes on first start."
echo ""
echo "To check status: docker compose ps"
echo "To view logs:    docker compose logs -f"
