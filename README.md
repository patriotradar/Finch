# Finch — AI Co-Founder (ASM Sales PWA)

Phone-first sales + admin dashboard. AI brain on Groq. Hosts on Vercel.

## Live
- `/` — Prospect chat
- `/admin` — Your dashboard

## Deploy (Vercel)
1. Import this repo at https://vercel.com/new
2. Add env vars from `.env.example`
3. Deploy

## Local
```bash
export GROQ_API_KEY=gsk_...
export FINCH_MEMORY_BACKEND=json
pip install -r requirements.txt
uvicorn web.chat_server:app --host 0.0.0.0 --port 8100
```
