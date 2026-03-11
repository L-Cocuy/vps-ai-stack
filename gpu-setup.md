# GPU Setup for Ollama

If your VPS has an NVIDIA GPU, you can enable hardware acceleration for significantly faster AI inference.

## Prerequisites

- NVIDIA GPU (RTX series, A-series, or similar)
- NVIDIA drivers installed on the host
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

## Install NVIDIA Container Toolkit

```bash
# Add the NVIDIA repo
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list \
  | tee /etc/apt/sources.list.d/nvidia-docker.list

apt-get update
apt-get install -y nvidia-container-toolkit

# Restart Docker
systemctl restart docker
```

## Enable GPU in docker-compose.yml

Uncomment the GPU section in the `ollama` service:

```yaml
ollama:
  ...
  deploy:
    resources:
      reservations:
        devices:
          - capabilities: [gpu]
```

Then restart:

```bash
docker compose down && docker compose up -d
```

## Verify GPU is being used

```bash
docker exec ollama ollama run llama3.2 "Hello"
docker exec ollama nvidia-smi
```

You should see GPU memory usage increase while running a model.

## VPS Providers with GPU Options

| Provider | GPU Options | Notes |
|---|---|---|
| Hetzner | None currently | CPU-only, great value |
| Vultr | NVIDIA A100 | Expensive |
| Lambda Labs | A10, A100 | Best GPU/price ratio |
| RunPod | Various | Pay-per-use, flexible |

For most small business use cases, a CPU-only VPS with 8GB RAM is sufficient for models up to 8B parameters.
