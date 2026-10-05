# Judge0 v1.13.1 on AWS Lightsail — Setup Guide

Spin up an Ubuntu 22.04 Lightsail instance, SSH in, and run Judge0 v1.13.1 with Docker.
For using the API once it's running, see [JUDGE0_API.md](JUDGE0_API.md).

Placeholders: `<IP>` = instance static IP, `<KEY>.pem` = your SSH key, `<TOKEN>` = your Judge0 auth token.

---

## 1. Create the instance

Console: https://lightsail.aws.amazon.com → **Create instance**

| Setting | Value |
|---|---|
| Region | Your nearest (e.g. Mumbai `ap-south-1`) |
| Platform / Blueprint | **Linux/Unix** → **OS Only** → **Ubuntu 22.04 LTS** |
| SSH key | Default key for the region, or **Create custom key** (download it immediately — Lightsail won't keep it) |
| Network | **Dual-stack** (IPv6-only can't take a static IP) |
| Plan | **$7 — 1 GB RAM / 2 vCPU / 40 GB SSD** |
| Name | e.g. `judge0` |

> 1 GB is enough for testing and light use **only with swap** (step 5). For real load (tens of concurrent candidates), resize — see §8.

## 2. Static IP

The default public IP changes on every stop/start.

**Networking** (left nav) → **Create static IP** → same region → attach to the instance → **Create**.
Free while attached; ~$0.005/hr if left unattached, so delete unused ones.

## 3. Firewall

Instance → **Networking** tab → **IPv4 Firewall** → **Add rule**:

- Application **Custom**, Protocol **TCP**, Port **2358**
- Tick **Restrict to IP address** and enter your backend server's IP (recommended — Judge0 runs over plain HTTP)

Keep SSH (22) open. If you restrict 22 to an IP, also tick **Allow Lightsail browser SSH/RDP**.

## 4. SSH in

Default key: top-right account menu → **Account** → **SSH keys** → **Default keys** → download for your region
(`LightsailDefaultKey-<region>.pem`).

```bash
chmod 400 <KEY>.pem
ssh -i ./<KEY>.pem ubuntu@<IP>
```

Windows: same `ssh` command works in PowerShell (built-in OpenSSH).
No key handy? Instance page → **Connect** tab → **Connect using SSH** (browser terminal).

## 5. Prepare the server

### Swap (needed on 1 GB)

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

### Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ubuntu
sudo apt install -y unzip
```

### Switch to cgroup v1 (required)

Judge0 1.13.1's sandbox (`isolate`) needs cgroup v1. Ubuntu 22.04 boots cgroup v2.
**If you skip this, the API responds normally but every submission fails** (status 13 "Internal Error", or `/box/script.py: No such file or directory`).

```bash
sudo nano /etc/default/grub
```

Edit the `GRUB_CMDLINE_LINUX=` line (**not** `GRUB_CMDLINE_LINUX_DEFAULT` — on Lightsail that one is overridden by `/etc/default/grub.d/50-cloudimg-settings.cfg`):

```
GRUB_CMDLINE_LINUX="systemd.unified_cgroup_hierarchy=0"
```

```bash
sudo update-grub
sudo reboot
```

Reconnect, then verify:

```bash
stat -fc %T /sys/fs/cgroup/        # must print: tmpfs   (cgroup2fs = still v2)
docker info | grep -i cgroup       # Cgroup Version: 1
```

## 6. Install Judge0

```bash
wget https://github.com/judge0/judge0/releases/download/v1.13.1/judge0-v1.13.1.zip
unzip judge0-v1.13.1.zip
cd judge0-v1.13.1
```

### Configure `judge0.conf`

Generate secrets (hex avoids special-character issues):

```bash
openssl rand -hex 32   # run 3 times: Redis password, Postgres password, auth token
```

Set these in `judge0.conf`:

```ini
REDIS_PASSWORD=<generated>
POSTGRES_PASSWORD=<generated>
AUTHN_HEADER=X-Auth-Token
AUTHN_TOKEN=<TOKEN>

# Tuning for the 1 GB plan
COUNT=2
MAX_QUEUE_SIZE=500
RAILS_MAX_THREADS=5
```

Store the secrets somewhere safe outside the repo. Leave `REDIS_HOST`, `POSTGRES_HOST`, `POSTGRES_DB` and `POSTGRES_USER` as they are.

> Set `POSTGRES_PASSWORD` correctly **before** the first start. It's baked into the DB volume on first boot; changing it later breaks the DB login.

### Start

```bash
docker compose up -d db redis
sleep 10
docker compose up -d
docker compose ps
```

The DB migrates automatically on boot. **Don't** run `docker compose run --rm server rails db:migrate`. It skips the script that loads `judge0.conf` and fails with a Postgres socket error.

## 7. Verify

```bash
curl -H "X-Auth-Token: <TOKEN>" http://localhost:2358/about
curl -H "X-Auth-Token: <TOKEN>" http://localhost:2358/workers     # "available" > 0

curl -s -X POST "http://localhost:2358/submissions?base64_encoded=false&wait=true" \
  -H "Content-Type: application/json" -H "X-Auth-Token: <TOKEN>" \
  -d '{"source_code":"print(\"hello\")","language_id":71}'
```

Expect `"stdout":"hello\n"` and `"status":{"id":3,"description":"Accepted"}`.
Then repeat from your own machine against `http://<IP>:2358` to confirm the firewall rule.

## 8. Operations

| Task | Command / action |
|---|---|
| Logs | `docker compose logs -f workers` (or `server`) |
| Apply `judge0.conf` changes | `docker compose up -d --force-recreate server workers`. A plain `up -d` won't pick up edits to the mounted file. |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Wipe all data | `docker compose down -v` (deletes every submission) |
| Resize to a bigger plan | Instance → **Snapshots** → **Create snapshot**. Then on the snapshot, **⋮** → **Create new instance**, pick a larger plan, move the static IP across, re-add the firewall rule, and delete the old instance. Raise `COUNT` to match the new plan. |

## Troubleshooting

| Symptom | Cause |
|---|---|
| Status 13 Internal Error / `/box/...` not found | Still on cgroup v2. Re-check §5 with `stat -fc %T /sys/fs/cgroup/`. |
| Submissions stuck "In Queue" | Workers are down, or the Redis/Postgres password doesn't match. Check `docker compose logs workers`. |
| Every request returns 401 | Missing or wrong `X-Auth-Token`. With `AUTHN_TOKEN` set, even `/about` needs the header. |
| Can't reach port 2358 from outside | Lightsail firewall rule is missing, or it's restricted to a different IP. |
| Slow or OOM under load | 1 GB plan is too small. Check `free -h`, then resize (§8). |
