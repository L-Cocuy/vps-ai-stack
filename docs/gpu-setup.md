# GPU Setup for Ollama

Ollama runs on CPU by default. If your VPS has an NVIDIA GPU, you can enable GPU acceleration for much faster inference.

## Prerequisites

- Linux VPS with an NVIDIA GPU
- NVIDIA drivers installed on the host
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- Docker Engine already working on the host

## Install the NVIDIA Container Toolkit

On Ubuntu/Debian:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

## Enable GPU access in Compose

Open [docker-compose.yml](../docker-compose.yml) and uncomment the `gpus: all` line under the `ollama` service:

```yaml
ollama:
  ...
  # gpus: all
```

After editing the file:

```bash
docker compose up -d
```

## Verify GPU access

Check that Docker can see the GPU:

```bash
docker exec ollama nvidia-smi
```

Then run a quick model test:

```bash
docker exec ollama ollama run llama3.2 "Hello"
```

## Notes

- GPU support is optional. The default template stays CPU-friendly.
- Small VPS deployments often work better with smaller models such as `llama3.2`.
- If `nvidia-smi` fails inside the container, re-check the host drivers and NVIDIA Container Toolkit setup.
