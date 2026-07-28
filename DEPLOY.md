# Host Finch for Free — Works Entirely From Your Phone

Finch is meant to run on **Oracle Cloud free tier** (forever free). For a quick phone test
from this sandbox you do **not** need Cloudflare paid plans.

## Right now (demo / this machine) — free tunnel, no card

While Finch is running locally:

```bash
./start_local.sh          # brain + web on :8100
./bin/start_public.sh     # free public URL (localtunnel / *.loca.lt)
```

Open the printed `https://….loca.lt` link on your phone:

| Link | What |
|------|------|
| `https://….loca.lt/` | Prospect chat with Finch |
| `https://….loca.lt/admin` | Your dashboard (password default `finch`) |

- **No Cloudflare account. No payment.**
- If the phone shows a **tunnel password** page, type the server IP printed by the script.
- The URL dies when the tunnel process or sandbox stops. It is only for chatting now.

Cloudflare *Tunnel* quick links (`trycloudflare.com`) are also free with no login.
Paid Cloudflare products (named tunnels, Zero Trust seats, domains) are **not** required.

---

## Permanent free host (use when you are ready)

Finch runs on Oracle Cloud's free tier. You interact through a web app
that you install on your phone's home screen. No Telegram. No app store. No monthly fees.


## How It Works

```
Your Phone (Chrome/Safari)
    │
    ├─ finch.security/       ← Prospects chat with Finch
    ├─ finch.security/admin  ← Your dashboard (pipeline, handoffs, docs)
    │
    ▼
Oracle Cloud Free Tier VM (4 cores, 24 GB RAM, 200 GB storage)
    ├─ Finch daemon (Python)
    ├─ Ollama + llama3 8B (local LLM, no API costs)
    ├─ ChromaDB (persistent memory)
    ├─ IMAP email listener (replies to prospects)
    └─ Web chat server (FastAPI + WebSockets)
         │
         ▼
    Internet — scans targets, sends cold emails, receives replies
```

## Step 1: Free Server (Oracle Cloud — Forever Free)

1. Go to https://signup.cloud.oracle.com
2. Sign up (they verify with a card, **never charge** on free tier)
3. Create VM Instance:
   - **Image:** Ubuntu 22.04 or 24.04
   - **Shape:** VM.Standard.AArch64.Flex (ARM Ampere)
   - **OCPU:** 4 | **Memory:** 24 GB | **Boot volume:** 200 GB
   - Save your SSH key
4. Note your public IP

> Oracle's free tier is the only free option with enough RAM to run a local LLM.
> If you can't get Oracle, see "Paid Alternatives" at the bottom.

## Step 2: Point a Domain (Optional but Professional)

Get a domain from Namecheap/Porkbun (~$5/year) and point it to your server IP:

```
Type: A
Name: finch  (or @ for root)
Value: <YOUR_SERVER_IP>
TTL: 300
```

If you skip this, use your server's raw IP address (less professional for cold emails).

## Step 3: Install Everything

```bash
ssh ubuntu@<YOUR_SERVER_IP>

# Upload the Finch project (from your computer)
scp -r /path/to/finch ubuntu@<YOUR_SERVER_IP>:~/finch

# Or clone it from your private repo
git clone <your-repo-url> finch
cd finch

# One command
chmod +x setup.sh
./setup.sh
```

## Step 4: Configure

```bash
# Required — Gmail with App Password (free)
# Create one at: Gmail → Settings → Security → 2FA → App Passwords
export FINCH_EMAIL="your-business@gmail.com"
export FINCH_EMAIL_PASSWORD="abcd efgh ijkl mnop"

# Optional — password for your admin dashboard
export FINCH_ADMIN_PASSWORD="your-secure-password"

# Add to ~/.bashrc so they persist after reboot
echo 'export FINCH_EMAIL="your-business@gmail.com"' >> ~/.bashrc
# ... etc for all env vars
```

## Step 5: Pull the LLM

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Download llama3 8B (~4.7 GB, takes a few minutes)
ollama pull llama3:8b
```

## Step 6: Start Finch

```bash
source venv/bin/activate

# Daemon mode — web chat + email + autonomous cycles
python main.py --daemon &

# Start email listener separately (IMAP IDLE)
python messaging/email_listener.py &

# Start the web server (your phone interface)
python web/chat_server.py &
```

For 24/7 uptime, use systemd:

```bash
sudo cp finch.service /etc/systemd/system/
sudo systemctl enable finch
sudo systemctl start finch
```

## Step 7: Add to Your Phone's Home Screen

### iPhone (Safari)
1. Open `https://finch.security/admin` (or `http://<IP>:8100/admin`)
2. Tap the Share button (box with arrow)
3. Scroll down → **Add to Home Screen**
4. Name it "Finch" → **Add**

### Android (Chrome)
1. Open `https://finch.security/admin`
2. Tap the three-dot menu → **Add to Home Screen**
3. Name it "Finch" → **Add**

It now looks and works like a real app — full screen, no browser chrome, app icon.

## Your Phone Workflow

| When | You see on your phone |
|------|----------------------|
| **Morning** | Open the Finch app. Status tab shows active conversations, pending handoffs, stored docs. |
| **Prospect replies to cold email** | Finch auto-responds. You see the conversation count go up in the Status tab. |
| **Prospect says "let's do it"** | Appears in the Handoffs tab: "Sarah Chen, BigBank, $1,970/mo, sarah@bigbank.com" |
| **You upload the contract** | Switch to Docs tab, upload from your phone. Tag it "contract". |
| **Send contract to prospect** | Switch to Chat tab, type: `/send 3 to sarah@bigbank.com` |
| **Evening check** | Open the app. "2 new handoffs, 5 active conversations." |

## The Three Interfaces

| URL | Who | Purpose |
|-----|-----|---------|
| `finch.security/` | **Prospects** | Chat widget. Finch sells for you. |
| `finch.security/admin` | **You (phone)** | Dashboard: pipeline, handoffs, docs, co-founder chat |
| `finch.security/admin` Chat tab | **You** | Talk to Finch directly. Commands, questions, status. |

## Email Reply Loop

This is how cold emails turn into closed deals without you touching anything:

```
1. Finch sends cold email from your-business@gmail.com
       ↓
2. Prospect replies: "Who is this? Tell me more."
       ↓
3. IMAP listener detects reply → routes to SalesConversation engine
       ↓
4. Harold responds accurately as the Aegis customer software assistant.
       ↓
5. 6-8 messages later: "Let's do it. Send the contract to sarah@bigbank.com"
       ↓
6. Appears in your Handoffs tab. You upload contract. Finch sends it.
```

## What About Documents?

Finch stores documents you upload through the admin dashboard. The workflow:

1. Prospect says yes → appears in Handoffs tab
2. You upload the contract PDF from your phone (Docs tab → upload)
3. Chat tab: `/send 3 to sarah@bigbank.com`
4. Finch emails it from your Gmail as "Harold Finch"

## Security

- Admin dashboard is password-protected (`FINCH_ADMIN_PASSWORD`)
- Prospects only see the public chat at `/` — they can't access `/admin`
- All data stays on your server. Nothing goes to third parties.
- Set up firewall: `sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 8100/tcp && sudo ufw enable`

## Paid Alternative (If Oracle Doesn't Work)

Hetzner VPS ($4-6/month) + OpenAI API:

```yaml
# config.yaml
llm:
  provider: "openai"
  model: "gpt-4o-mini"   # $0.15/million tokens
  api_key: "${OPENAI_API_KEY}"
```

GPT-4o-mini costs ~$0.30-0.50/day for Finch's full workload. At $4/month (server) + $10/month (API) = **$14/month total**.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Ollama won't start | `ollama serve &` — may need 8+ GB free RAM |
| Email replies not sending | Check Gmail App Password is 16 chars, no spaces |
| Phone can't connect | Make sure port 8100 is open: `sudo ufw allow 8100/tcp` |
| PWA won't install | Must be HTTPS. Use Let's Encrypt: `sudo apt install certbot && certbot certonly --standalone -d finch.security` |
