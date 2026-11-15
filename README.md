# Crayon Web

Public web client and Python backend for Crayon, an AI assistant.
Live: https://crayon-web.onrender.com

## What it does

- Web chat as an installable PWA, backed by a FastAPI server
- LangChain LLM layer: Gemini by default, OpenRouter as an option
- Live web search and page fetch for current questions (news, prices, scores), answered with sources and dates
- Sandboxed Python computer (Pyodide) that runs inside the visitor's browser for calculations, data and file work
- Image generation
- Per-visitor memory across visits (optional Postgres), with a "Forget me" button that wipes everything saved
- Per-IP rate limits so it survives on free tiers

## Channels

The same backend also serves a Telegram bot, a Discord bot, WhatsApp (Twilio and D360 adapters), Gmail sign-in and voice replies.

## Android

An Android TWA package is published under Releases; `assetlinks.json` and the app icons live in this repo.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # add your keys
uvicorn app:app
```

Built with a teammate as a learning project.
