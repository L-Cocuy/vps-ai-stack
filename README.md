# VPS AI Stack

Practical self-hosted AI stack template for a single Linux VPS.

Run Open WebUI, Ollama, n8n, Postgres, Traefik, and an internal OCR API with a simple Docker Compose workflow, HTTPS by default, and baseline ops scripts that are realistic for small consulting deployments.

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
- Runs an internal-only OCR microservice for PDFs and receipt images (`n8n -> OCR -> Ollama` workflow pattern).
- Includes preflight, backup, restore, health, and hardening audit scripts for basic operations.

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
│   ├── hardening-check.sh
│   ├── health-report.sh
│   ├── preflight.sh
│   ├── restore.sh
│   └── setup.sh
├── services/
│   └── ocr-api/
│       ├── Dockerfile
│       ├── main.py
│       └── requirements.txt
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
| OCR API (internal) | PDF/image text extraction service for n8n | 8081 |
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
- OCR for scanned PDFs and receipt photos is CPU-intensive. For frequent OCR workloads, prefer 4 vCPU and 8-12 GB RAM.
- First model downloads need extra disk headroom. The preflight script requires at least 20 GB free.
- NVIDIA GPU support is optional. See [GPU setup](docs/gpu-setup.md).

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
- pull pinned images
- build the local OCR API image
- start the stack

### 5. Access the services

| Service | URL |
|---|---|
| Open WebUI | `https://chat.example.com` |
| n8n | `https://automation.example.com` |

Expected first-start behavior:

- Let's Encrypt can take 1 to 3 minutes after DNS is correct.
- Ollama model bootstrap can take several minutes on the first run.
- Open WebUI may not be usable until the default model pull finishes.
- OCR image build on first run can add a few minutes depending on VPS network speed.

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

## Internal API Endpoints for n8n

Inside this Docker Compose network (`ai-stack`), n8n should use internal service DNS names:

- Ollama base URL: `http://ollama:11434`
- OCR API base URL: `http://ocr:8081`
- n8n environment shortcuts: `N8N_OLLAMA_BASE_URL` and `N8N_OCR_BASE_URL`

This is already consistent with the stack design:

- `ollama`, `n8n`, and `ocr` share the same user-defined Docker network (`ai-stack`)
- Ollama exposes port `11434` internally
- OCR exposes port `8081` internally
- neither Ollama nor OCR is published directly to the public internet

The health script now checks both internal paths from the n8n container:

- `http://ollama:11434/api/tags`
- `http://ocr:8081/health`

### OCR API contract

Endpoint:

```text
POST http://ocr:8081/v1/extract
Content-Type: multipart/form-data
```

Quick test from inside the Docker network:

```bash
docker run --rm --network ai-stack -v "$PWD:/work:ro" curlimages/curl:8.14.1 \
  -sS -X POST http://ocr:8081/v1/extract \
  -F "file=@/work/sample-receipt.jpg"
```

Request fields:

- `file`: required for normal n8n usage (binary file upload)
- `languages`: optional, defaults to `eng`
- `file_path`: optional advanced mode (absolute path under `OCR_ALLOWED_PATHS`); requires you to add a shared mount, and must not be combined with `file`

Example success response:

```json
{
  "success": true,
  "text": "extracted text...",
  "source_type": "pdf",
  "extraction_mode": "direct_text",
  "mime_type": "application/pdf",
  "character_count": 1234,
  "confidence": null,
  "warnings": [],
  "error": null,
  "processing_ms": 217
}
```

Response behavior:

- `extraction_mode=direct_text` for PDFs with sufficient embedded text
- `extraction_mode=ocr` for scanned PDFs and image inputs
- `confidence` is populated for image OCR when available (0.0-1.0); PDF OCR confidence is typically `null`
- `warnings` explains fallback decisions and edge conditions

### Recommended n8n pattern

1. n8n ingests a receipt/document file.
2. n8n sends the binary file to `POST http://ocr:8081/v1/extract`.
3. OCR returns plain extracted text plus extraction metadata.
4. n8n sends the extracted text to Ollama at `http://ollama:11434` for structured field extraction.

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

Create a health report:

```bash
bash scripts/health-report.sh
```

Audit host hardening:

```bash
sudo bash scripts/hardening-check.sh
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

OCR persistence note:

- The OCR service is stateless in this template.
- No OCR named volume is created.
- Backup/restore scripts do not need OCR-specific volume changes.
- Existing persisted volumes (`ollama_data`, `openwebui_data`, `n8n_data`, `postgres_data`, `traefik_certs`) remain unchanged.

By default, backups stay local only. If you enable remote mode in `.env`, the script also:

- creates an encrypted `tar.gz.enc` bundle of the timestamped backup folder
- uploads that encrypted file to S3, Backblaze B2, or an `rsync` target
- keeps the original local backup flow unchanged

Remote backup settings in [env.example](env.example):

```env
BACKUP_REMOTE_ENABLED=false
BACKUP_REMOTE_TYPE=s3
BACKUP_PASSPHRASE=replace_with_a_long_unique_passphrase
BACKUP_S3_BUCKET=your-bucket
BACKUP_S3_PREFIX=vps-ai-stack
BACKUP_S3_REGION=us-east-1
BACKUP_S3_ACCESS_KEY_ID=replace_me
BACKUP_S3_SECRET_ACCESS_KEY=replace_me
```

Supported remote modes:

- `s3`: uses the AWS CLI in a helper container
- `b2`: uses the Backblaze B2 S3-compatible endpoint
- `rsync`: uses the host `rsync` client over SSH

If remote mode is enabled but incomplete, the script still finishes the local backup first and then exits with a clear error for the remote step.

The restore script:

- stops the stack first
- restores the named volumes from the selected backup
- saves your current `.env` as `.env.pre-restore.<timestamp>` before replacing it
- starts the stack again

Remote restore is intentionally simple: download the encrypted archive, decrypt it, extract it back under `backups/`, and then run `bash scripts/restore.sh`.

Take a fresh backup before intentional upgrades.

## Safe Upgrade for Existing Deployments

For already-running stacks, use this conservative upgrade flow:

1. Take a fresh backup first:

   ```bash
   bash scripts/backup.sh
   ```

2. Pull pinned upstream images:

   ```bash
   docker compose pull
   ```

3. Build the OCR API image (local Dockerfile) and recreate services:

   ```bash
   docker compose build ocr
   docker compose up -d
   ```

4. Validate health:

   ```bash
   docker compose ps
   bash scripts/health-report.sh
   ```

Why existing data remains intact:

- Docker named volumes are separate from container lifecycles.
- This upgrade does not rename or remove existing named volumes.
- Existing service names and volume mappings for `n8n`, `Postgres`, `Open WebUI`, `Ollama`, and `Traefik` are preserved.
- `docker compose pull` + `docker compose up -d` replaces containers but reattaches the same named volumes.

When data could be lost or orphaned:

- if you manually delete a named volume (`docker volume rm ...`)
- if you change volume names in Compose without migrating data
- if you run destructive cleanup commands that prune required volumes
- if you restore from an incomplete or wrong backup set

## Image Versions

This template pins explicit image versions instead of `latest` or `main`.

Current pins in [docker-compose.yml](docker-compose.yml):

- Traefik `v3.6.7`
- Ollama `0.13.5`
- Open WebUI `v0.7.2`
- n8n `2.2.6`
- Postgres `16.13-alpine3.23`
- OCR API base image `python:3.12.11-slim-bookworm` with pinned Python dependencies in `services/ocr-api/requirements.txt`

To update intentionally:

1. Review the upstream release notes for each service.
2. Edit the image tags in [docker-compose.yml](docker-compose.yml).
3. Take a backup with `bash scripts/backup.sh`.
4. Run `docker compose pull`.
5. Rebuild local OCR image when OCR service code or dependencies changed: `docker compose build ocr`.
6. Run `docker compose up -d`.
7. Verify health with `docker compose ps`, `docker compose logs`, and `bash scripts/health-report.sh`.

## Hardening Baseline

This repository is a deployment template, not a full security program.

What is now automated in the stack:

- HTTPS termination through Traefik
- baseline security headers on Open WebUI and n8n
- baseline request rate limiting on Open WebUI and n8n
- optional CIDR allowlist support for n8n via `N8N_IP_ALLOWLIST`
- health and hardening audit scripts under `scripts/`

`N8N_IP_ALLOWLIST` applies to the entire `automation.<domain>` host in this template, including editor access and incoming webhook traffic. Leave it blank if you need public webhook ingress.

What is still manual on the host:

- use SSH keys only
- disable root SSH login
- disable password authentication
- enable UFW and allow only `22`, `80`, and `443`
- install and enable fail2ban
- enable unattended security updates
- generate strong secrets instead of reusing example values

To verify the host baseline after hardening:

```bash
sudo bash scripts/hardening-check.sh
```

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

## Monitoring Hooks

Run the built-in health report manually:

```bash
bash scripts/health-report.sh
```

The report checks:

- `docker compose ps`
- core container status and health
- n8n internal connectivity to Ollama and OCR APIs
- disk usage against `HEALTH_DISK_WARN_PCT` and `HEALTH_DISK_FAIL_PCT`
- presence of `acme.json` in the Traefik certificate volume

The script exits non-zero when a critical issue is found.

Example cron entries:

```cron
0 * * * * cd /opt/vps-ai-stack && bash scripts/health-report.sh >> /var/log/vps-ai-stack-health.log 2>&1
15 2 * * * cd /opt/vps-ai-stack && bash scripts/backup.sh >> /var/log/vps-ai-stack-backup.log 2>&1
```

If you want alerting, point the health check cron output at your preferred mailer, logger, or webhook wrapper.

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

### OCR returns empty or low-quality text

Symptoms:
- OCR responses contain very little text
- receipt totals/vendor names are missing

What to do:
- ensure input files are high-contrast and not blurry
- set `OCR_LANGUAGES` in `.env` if documents are not English
- tune `OCR_TESSERACT_PSM` (for receipts, `4` or `6` are common)
- review OCR warnings in the API response and adjust workflow retries

### n8n cannot reach internal Ollama or OCR endpoint

Symptoms:
- n8n HTTP Request nodes to `http://ollama:11434` or `http://ocr:8081` fail
- `scripts/health-report.sh` reports internal connectivity failures

What to do:
- run `docker compose ps` and confirm `ollama`, `n8n`, and `ocr` are running
- verify all three services are attached to network `ai-stack`
- check `docker compose logs n8n ollama ocr`
- rerun `bash scripts/health-report.sh` after recovery

## License

[AGPL-3.0](LICENSE)

## Author

Built by [Juan Mejia](https://at.linkedin.com/in/juan-mejia-engineering-consulting) — Systems Engineer and AI consultant.
