# 🔒 VPS AI Stack

**A production-ready, privacy-first AI platform for small businesses.**  
Run your own private AI — no subscriptions, no data leaving your server, no vendor lock-in.

![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)
![Status](https://img.shields.io/badge/Status-Production--Ready-green)

---

## Why This Exists

Most AI tools send your business data to third-party servers. For many small businesses — law firms, clinics, accountants, consultancies — that's a compliance problem, a competitive risk, or simply unacceptable.

This stack gives you a **fully self-hosted AI platform** on any VPS you control:

| What you get | What you avoid |
|---|---|
| Private AI chat (like ChatGPT, but yours) | Monthly SaaS subscriptions |
| Workflow automation with AI built in | Data sent to OpenAI / Google / Microsoft |
| Persistent memory & document storage | Vendor lock-in |
| SSL-secured, domain-based access | Complex DevOps setup |

---

## What's Included

```
┌─────────────────────────────────────────────────────────┐
│                      Your Domain                        │
│                                                         │
│   chat.yourdomain.com        automation.yourdomain.com  │
│          │                            │                 │
│    ┌─────▼──────┐              ┌──────▼─────┐           │
│    │ Open WebUI │              │    n8n     │           │
│    │  (AI Chat) │              │ (Workflows)│           │
│    └─────┬──────┘              └──────┬─────┘           │
│          │                            │                 │
│    ┌─────▼──────┐              ┌──────▼─────┐           │
│    │   Ollama   │              │  Postgres  │           │
│    │ (LLM Engine│              │ (Database) │           │
│    └────────────┘              └────────────┘           │
│                                                         │
│              ┌──────────────┐                           │
│              │   Traefik    │                           │
│              │ (SSL + Proxy)│                           │
│              └──────────────┘                           │
└─────────────────────────────────────────────────────────┘
```

| Service | Purpose | Port (internal) |
|---|---|---|
| [Traefik](https://traefik.io/) | Reverse proxy + automatic SSL | 80, 443 |
| [Ollama](https://ollama.com/) | Local LLM engine (runs AI models) | 11434 |
| [Open WebUI](https://github.com/open-webui/open-webui) | Chat interface + document Q&A | 8080 |
| [n8n](https://n8n.io/) | Workflow automation with AI nodes | 5678 |
| [PostgreSQL](https://www.postgresql.org/) | Persistent storage for all services | 5432 |

---

## Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 40 GB SSD | 80 GB SSD |
| OS | Ubuntu 22.04+ | Ubuntu 22.04+ |
| Domain | Required | Required |

> **GPU support:** Ollama can use NVIDIA GPUs for dramatically faster inference.  
> See [GPU Setup](docs/gpu-setup.md) for instructions.

---

## Quick Start

### 1. Point your DNS to your VPS

Create two DNS A records at your domain registrar:

```
chat.yourdomain.com       → YOUR_VPS_IP
automation.yourdomain.com → YOUR_VPS_IP
```

### 2. Clone and run the setup script

```bash
git clone https://github.com/yourusername/vps-ai-stack.git
cd vps-ai-stack
sudo bash scripts/setup.sh
```

The script will:
- Install Docker if needed
- Create your `.env` configuration file
- Prompt you to set your domain and passwords
- Start all services
- Issue SSL certificates automatically

### 3. Access your stack

| Service | URL |
|---|---|
| AI Chat | `https://chat.yourdomain.com` |
| Automation | `https://automation.yourdomain.com` |

On first launch, Open WebUI will ask you to create an admin account.

---

## Configuration

Copy `.env.example` to `.env` and set your values:

```bash
cp .env.example .env
nano .env
```

The only required settings:

```env
DOMAIN=yourdomain.com
POSTGRES_PASSWORD=a_strong_password
WEBUI_SECRET_KEY=run_openssl_rand_hex_32
N8N_ENCRYPTION_KEY=run_openssl_rand_hex_32
ACME_EMAIL=you@yourdomain.com
```

---

## Default AI Models

On first start, the stack automatically downloads:

- **llama3.2** — General purpose chat and reasoning (2B, fast)
- **nomic-embed-text** — Text embeddings for document search

To add more models, open `https://chat.yourdomain.com` → Settings → Models, or via CLI:

```bash
docker exec ollama ollama pull mistral
docker exec ollama ollama pull codellama
```

Browse available models at [ollama.com/library](https://ollama.com/library).

---

## Useful Commands

```bash
# Start the stack
docker compose up -d

# Stop the stack
docker compose down

# View live logs
docker compose logs -f

# Update all services to latest versions
docker compose pull && docker compose up -d

# Backup your data volumes
bash scripts/backup.sh
```

---

## Security Notes

- All traffic is encrypted via HTTPS (Let's Encrypt)
- New user signups are **disabled by default** — only the admin can add users
- No ports are exposed except 80 and 443
- All inter-service communication is on an internal Docker network
- Your AI conversations and documents never leave your server

---

## Roadmap

- [ ] Automated daily backups script
- [ ] GPU auto-detection in setup script
- [ ] Lightweight monitoring dashboard (Uptime Kuma)
- [ ] One-click model selection wizard
- [ ] Restore script for backups

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first.

---

## License

[AGPL-3.0](LICENSE) — Free to use and modify. If you run this as a hosted service, you must open source your modifications. For commercial licensing, contact the author.

---

## Author

Built by [Juan Mejia](https://at.linkedin.com/in/juan-mejia-engineering-consulting) — Systems Engineer & AI Consultant.  
If this saved you time, consider starring the repo ⭐