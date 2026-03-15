# VPS AI Stack

Practical self-hosted AI stack template for a single Linux VPS.

Run Open WebUI, Ollama, n8n, Postgres, and Traefik with a simple Docker Compose workflow, HTTPS by default, and a few baseline ops scripts that are realistic for small consulting deployments.

![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)
![Status](https://img.shields.io/badge/Status-VPS--Template-0A7B83)

## Why This Exists

Many SMB teams want private AI tools without handing internal data to a third-party SaaS. This repository is a starting point for that use case: one VPS, one domain, beginner-friendly commands, and a stack that is small enough to understand and support.

## What This Template Does

- Publishes only `80` and `443` to the internet through Traefik.
- Routes `chat.<your-domain>` to Open WebUI and `automation.<your-domain>` to n8n.
- Uses Let's Encrypt HTTP challenge for certificates.
- Keeps service data in named Docker volumes.
- Bootstraps two default Ollama models after the API is actually ready.
- Includes preflight, backup, and restore scripts for basic operations.

## What This Template Does Not Do

- It is not a compliance package or certification-ready control set.
- It does not include HA, clustering, Kubernetes, managed backups, or external monitoring.
- It does not harden your VPS automatically beyond sane container defaults.
- It does not guarantee model performance on small VPS plans.
- It does not make Open WebUI or n8n multi-tenant or enterprise-governed by itself.

## Stack Layout

```text
.
├── configs/
│   └── traefik/
│       └── traefik.yml
├── docs/
│   └── gpu-setup.md
├── scripts/
│   ├── backup.sh
│   ├── preflight.sh
│   ├── restore.sh
│   └── setup.sh
├── docker-compose.yml
├── env.example
└── README.md
```

| Service | Purpose | Internal port |
|---|---|---|
| [Traefik](https://traefik.io/) | Reverse proxy and TLS termination | 80, 443 |
| [Ollama](https://ollama.com/) | Local LLM runtime | 11434 |
| [Open WebUI](https://github.com/open-webui/open-webui) | Browser chat UI | 8080 |
| [n8n](https://n8n.io/) | Workflow automation | 5678 |
| [PostgreSQL](https://www.postgresql.org/) | n8n database backend | 5432 |

## Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 40 GB SSD | 80 GB SSD |
| OS | Ubuntu 22.04+ or similar Linux | Ubuntu 22.04+ |
| Domain | Required | Required |

Notes:
- 4 GB RAM is workable for light use, but low-memory VPS plans will feel slow during model pulls and first inference.
- First model downloads need extra disk headroom. The preflight script requires at least 20 GB free.
- NVIDIA GPU support is optional. See [docs/gpu-setup.md](/Volumes/dev_volume/vps-ai-stack/docs/gpu-setup.md).

## Quick Start

### 1. Create DNS records

Point both hostnames to your VPS public IP:

```text
chat.example.com       -> YOUR_VPS_IP
automation.example.com -> YOUR_VPS_IP
```

### 2. Clone the repo

```bash
git clone https://github.com/yourusername/vps-ai-stack.git
cd vps-ai-stack
```

### 3. Create `.env`

```bash
cp env.example .env
nano .env
```

Set real values before deployment:

```env
DOMAIN=example.com
POSTGRES_PASSWORD=replace_with_a_strong_password
WEBUI_SECRET_KEY=paste_32_byte_hex_secret_here
N8N_ENCRYPTION_KEY=paste_32_byte_hex_secret_here
ACME_EMAIL=ops@example.com
GENERIC_TIMEZONE=UTC
```

Generate the two secrets with:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

### 4. Run setup

```bash
sudo bash scripts/setup.sh
```

The setup script will:

- install Docker if it is missing
- install the Docker Compose plugin if needed
- fail early if `.env` still contains placeholders
- prepare the Traefik certificate volume and `acme.json` permissions
- validate the Compose file
- pull pinned images and start the stack

### 5. Access the services

| Service | URL |
|---|---|
| Open WebUI | `https://chat.example.com` |
| n8n | `https://automation.example.com` |

Expected first-start behavior:

- Let's Encrypt can take 1 to 3 minutes after DNS is correct.
- Ollama model bootstrap can take several minutes on the first run.
- Open WebUI may not be usable until the default model pull finishes.

## Preflight Checks

Run the preflight script any time before deployment:

```bash
bash scripts/preflight.sh
```

It checks:

- Linux host
- Docker installed and daemon reachable
- Docker Compose plugin available
- `.env` present and not left at default placeholder values
- DNS resolution for `chat.$DOMAIN` and `automation.$DOMAIN`
- local availability of ports `80` and `443`
- at least 20 GB free disk space

## Default Models

The bootstrap container pulls these models once Ollama is actually ready:

- `llama3.2`
- `nomic-embed-text`

The bootstrap step is safe to rerun. `ollama pull` is idempotent for already-present models.

To add more models later:

```bash
docker exec ollama ollama pull mistral
docker exec ollama ollama pull qwen2.5-coder:7b
```

Browse the Ollama model library at [ollama.com/library](https://ollama.com/library).

## Daily Operations

Common commands:

```bash
docker compose ps
docker compose logs -f
docker compose up -d
docker compose down
```

Manual preflight:

```bash
bash scripts/preflight.sh
```

Create a backup:

```bash
bash scripts/backup.sh
```

Restore the latest backup with confirmation:

```bash
bash scripts/restore.sh
```

Restore a specific backup without prompting:

```bash
bash scripts/restore.sh --yes backups/20260315-120000
```

## Backups and Restore

The backup script creates a timestamped folder under `backups/` and stores:

- `ollama_data.tar.gz`
- `openwebui_data.tar.gz`
- `n8n_data.tar.gz`
- `postgres_data.tar.gz`
- `traefik_certs.tar.gz`
- a copy of `.env`
- `manifest.txt`

The restore script:

- stops the stack first
- restores the named volumes from the selected backup
- saves your current `.env` as `.env.pre-restore.<timestamp>` before replacing it
- starts the stack again

Take a fresh backup before intentional upgrades.

## Image Versions

This template pins explicit image versions instead of `latest` or `main`.

Current pins in [docker-compose.yml](/Volumes/dev_volume/vps-ai-stack/docker-compose.yml):

- Traefik `v3.6.7`
- Ollama `0.13.5`
- Open WebUI `v0.7.2`
- n8n `2.2.6`
- Postgres `16.13-alpine3.23`

To update intentionally:

1. Review the upstream release notes for each service.
2. Edit the image tags in [docker-compose.yml](/Volumes/dev_volume/vps-ai-stack/docker-compose.yml).
3. Take a backup with `bash scripts/backup.sh`.
4. Run `docker compose pull`.
5. Run `docker compose up -d`.
6. Verify health with `docker compose ps` and `docker compose logs`.

## Hardening Baseline

This repository is a deployment template, not a full security program. At minimum, harden the VPS itself:

- use SSH keys only
- disable root SSH login
- disable password authentication
- enable UFW and allow only `22`, `80`, and `443`
- install and enable fail2ban
- enable unattended security updates
- generate strong secrets instead of reusing example values

Useful commands:

```bash
openssl rand -hex 32
openssl rand -base64 48
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo apt-get update
sudo apt-get install -y fail2ban unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

Do not describe this template as compliant, certified, or fully hardened unless you add and validate the controls required for your environment.

## Troubleshooting

### DNS has not propagated yet

Symptoms:
- `scripts/preflight.sh` says `chat.<domain>` or `automation.<domain>` does not resolve.
- Let's Encrypt never finishes.

What to do:
- confirm both DNS records point to the VPS public IP
- wait for propagation and rerun `bash scripts/preflight.sh`

### Let's Encrypt HTTP challenge fails

Symptoms:
- Traefik logs mention ACME or challenge errors
- HTTPS never comes up

What to do:
- confirm ports `80` and `443` are open to the internet
- make sure another web server is not already bound to those ports
- verify DNS points to this VPS, not an old host
- inspect `docker compose logs traefik`

### Low-memory VPS behavior

Symptoms:
- model pulls are slow
- the host starts swapping heavily
- UI feels unresponsive during first inference

What to do:
- use smaller Ollama models
- avoid concurrent model pulls
- increase RAM or add swap if appropriate for your risk profile

### Ollama model pulls take a long time or time out

Symptoms:
- `ollama-bootstrap` stays running for a while
- first startup takes much longer than expected

What to do:
- check `docker compose logs -f ollama-bootstrap`
- confirm the VPS still has free disk space
- retry with `docker compose up -d ollama-bootstrap`
- pull models manually with `docker exec ollama ollama pull <model>`

## License

[AGPL-3.0](/Volumes/dev_volume/vps-ai-stack/LICENSE)

## Author

Built by [Juan Mejia](https://at.linkedin.com/in/juan-mejia-engineering-consulting) — Systems Engineer and AI consultant.
