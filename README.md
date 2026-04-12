# AI SimTest Platform

**The open-source AI bot testing platform — now with a cloud you can afford.**

## Repository Structure

```
ai-simtest-platform/          # SaaS Platform (this repo)
├── packages/
│   ├── api/                   # FastAPI Control Plane API
│   ├── web/                   # Next.js Frontend
│   └── engine/                # Engine submodule (ai-simtest OSS repo)
├── infra/                     # Docker, Terraform, CI/CD
└── docs/                      # Documentation
```

## Architecture

- **Control Plane** (this repo): Auth, tenancy, config, billing, audit, UI, API
- **Execution Plane**: Workers that execute simulations using the engine
- **Engine** (separate repo): The OSS CLI simulation/evaluation core

## Tech Stack

| Component | Technology | Tier |
|-----------|-----------|------|
| Auth | Clerk | Free (Hobby) |
| Billing | Stripe | Pay-per-transaction |
| Database | Neon (PostgreSQL) | Free |
| API | FastAPI (Python 3.11+) | — |
| Frontend | Next.js + Tailwind + Shadcn/UI | — |
| Object Storage | Cloudflare R2 | Free |
| API Hosting | Railway | Free → $5/mo |
| Frontend Hosting | Vercel | Free (Hobby) |
| CI/CD | GitHub Actions | Free |

## Quick Start (Development)

```bash
# Clone with engine submodule
git clone --recursive https://github.com/bishnuprasadp/ai-simtest-platform.git
cd ai-simtest-platform

# API setup
cd packages/api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Fill in your Clerk + Neon credentials
alembic upgrade head
uvicorn src.main:app --reload

# Web setup (new terminal)
cd packages/web
npm install
cp .env.example .env.local  # Fill in Clerk publishable key
npm run dev
```

## License

- **Engine** (packages/engine/): MIT License (open source)
- **Platform** (everything else): Proprietary
