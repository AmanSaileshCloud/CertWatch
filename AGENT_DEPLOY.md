# CertWatch Agent Deployment Guide

The SSL Agent runs inside a customer's private network and pushes certificate
monitoring results to your central CertWatch server. No inbound access to the
customer network is required.

---

## Architecture Overview

```
Customer Private Network          Your CertWatch Server
┌─────────────────────┐          ┌──────────────────────┐
│  SSL Agent          │          │  FastAPI Backend      │
│  ↓ probes           │          │  /agent/report        │
│  app.internal:443   │          │  SQLite DB            │
│  db.internal:443    │          │  Dashboard            │
│       │             │          └──────────────────────┘
│  NAT Gateway        │──HTTPS──►
└─────────────────────┘  outbound only
```

---

## Part 1 — CertWatch Server Setup (your side)

### Step 1 — Add Agent API Key to .env

```bash
# On your CertWatch server
openssl rand -hex 32   # generate a strong key — copy the output
```

Add to `/etc/certwatch/certwatch.env`:
```
AGENT_API_KEY=paste-your-generated-key-here
```

### Step 2 — Restart the backend

```bash
sudo systemctl restart certwatch-api
```

### Step 3 — Verify the endpoint is live

Agents report **facts only** — was the host reachable, and what is the
certificate's `notAfter`. The server derives `days_remaining`, the status, and
the alert thresholds from those, using the same `THRESHOLD_DAYS` that govern
directly-monitored domains. Do not send a status: agent-supplied statuses are
ignored, so the agent can never drift from the server's definition of
"expiring".

```bash
curl -s http://localhost/agent/report \
  -X POST \
  -H "Content-Type: application/json" \
  -H "X-Agent-Key: your-key-here" \
  -d '{"agent_id":"test","domain":"example.com","port":443,"reachable":true,
       "not_after":"2026-12-31T23:59:59+00:00"}'
```

Expected response — note the server tells you what it concluded:

```json
{"ok": true, "domain": "example.com:443", "status": "ok", "days_remaining": 129}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `agent_id` | yes | Which agent is reporting (shown in the dashboard) |
| `domain` | yes | Bare hostname |
| `port` | no (443) | TLS port |
| `reachable` | yes | Could the agent complete the TLS handshake? |
| `not_after` | when reachable | Cert expiry, ISO-8601. Omit/null if unreachable |
| `error` | no | Why the probe failed, recorded verbatim |
| `private` | no | Whether the agent classified the host as internal |

A reachable host with no `not_after` is recorded as `unreachable` with the error
preserved — it never becomes a false "expiring".

---

## Part 2 — Agent Deployment (customer side)

Send the customer the `agent/` folder contents and their credentials.

### Option A — Run with Python (simplest)

**Step 1 — Install dependencies**
```bash
pip install cryptography pyyaml
# or
pip install -r requirements.txt
```

**Step 2 — Edit config.yaml**
```yaml
agent_id: "customer-agent-001"       # unique ID you assign per customer
api_url: "https://your-certwatch.com" # your CertWatch server URL
api_key: "your-generated-key"         # the key from Part 1 Step 1
interval_seconds: 300                 # check every 5 minutes
tls_timeout: 10.0

domains:
  - host: "app.internal.local"
    port: 443
  - host: "ppitest.internal.tpml.in"
    port: 443
  - host: "db.internal.company.com"
    port: 8443
```

**Step 3 — Run the agent**
```bash
python3 agent.py
```

---

### Option B — Run with Docker (recommended)

**Step 1 — Build the image**
```bash
cd agent/
docker build -t certwatch-agent .
```

**Step 2 — Edit config.yaml** (same as Option A Step 2)

**Step 3 — Run**
```bash
docker run -d \
  --name certwatch-agent \
  --restart always \
  -v $(pwd)/config.yaml:/agent/config.yaml \
  certwatch-agent
```

**Check logs:**
```bash
docker logs -f certwatch-agent
```

---

### Option C — Run as systemd service (production Linux)

**Step 1 — Install**
```bash
sudo mkdir -p /opt/certwatch-agent
sudo cp agent/* /opt/certwatch-agent/
cd /opt/certwatch-agent
pip3 install -r requirements.txt
```

**Step 2 — Edit config.yaml**
```bash
sudo nano /opt/certwatch-agent/config.yaml
```

**Step 3 — Create systemd service**
```bash
sudo nano /etc/systemd/system/certwatch-agent.service
```

Paste:
```ini
[Unit]
Description=CertWatch SSL Agent
After=network.target

[Service]
WorkingDirectory=/opt/certwatch-agent
ExecStart=/usr/bin/python3 agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Step 4 — Enable and start**
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now certwatch-agent
sudo systemctl status certwatch-agent
```

**Check logs:**
```bash
journalctl -u certwatch-agent -f
```

---

### Option D — Run in Kubernetes

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: certwatch-agent-config
data:
  config.yaml: |
    agent_id: "k8s-agent-001"
    api_url: "https://your-certwatch.com"
    api_key: "your-generated-key"
    interval_seconds: 300
    domains:
      - host: "app.internal.local"
        port: 443
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: certwatch-agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: certwatch-agent
  template:
    metadata:
      labels:
        app: certwatch-agent
    spec:
      containers:
      - name: agent
        image: certwatch/agent:latest
        env:
        - name: AGENT_CONFIG
          value: /config/config.yaml
        volumeMounts:
        - name: config
          mountPath: /config
      volumes:
      - name: config
        configMap:
          name: certwatch-agent-config
```

```bash
kubectl apply -f certwatch-agent.yaml
kubectl logs -f deployment/certwatch-agent
```

---

## Part 3 — Verify Results in Dashboard

After the agent runs its first check:

1. Open `http://your-certwatch-server` in your browser
2. Login as admin
3. You will see the private domains appear automatically in the dashboard
4. Status will show `ok`, `warning`, `expired`, or `unreachable` — computed by
   the server from the expiry the agent reported, against `THRESHOLD_DAYS`

Agent-reported domains are marked `source: "agent"` and are **never probed by
the central checker** — it cannot reach them, and probing would overwrite the
agent's data with a false `unreachable`, which would in turn suppress every
expiry alert for that host.

If an agent stops reporting for longer than `AGENT_STALE_AFTER_HOURS` (default
6), the checker raises a one-time alert naming the agent, and the domain's
`last_error` explains how long it has been silent. Expiry alerts keep firing
from the last known `notAfter` in the meantime, so a dead agent never silently
masks an expiring certificate.

---

## Part 4 — Multi-Customer Setup

For each new customer:

**1. Generate a unique API key:**
```bash
openssl rand -hex 32
```

**2. Add to your server .env** (comma-separated or use one shared key):
```
AGENT_API_KEY=key-for-customer-a
```

**3. Give the customer:**
- The `agent/` folder
- Their unique `agent_id` (e.g. `customer-acme-001`)
- Their `api_key`
- Your CertWatch `api_url`

**4. Customer runs the agent** — results appear in your dashboard tagged by `agent_id`

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Agent shows `Cannot reach API` | Verify `api_url` is correct and port 443 is open outbound |
| Dashboard shows `unreachable` | Agent can't reach the private domain — check DNS and port 443 |
| `Invalid agent key` error | `api_key` in config.yaml doesn't match `AGENT_API_KEY` on server |
| Agent not starting | Run `python3 agent.py` manually and check error output |
| Domains not appearing | Check `/agent/report` endpoint is registered — restart backend |

**Test agent manually (one probe, no loop):**
```bash
cd agent/
python3 -c "
from probe import probe
r = probe('your-internal-domain.com', 443)
print(r)
"
```
