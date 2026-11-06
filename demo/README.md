# Synthetic lending dashboard

This portfolio demo shows how a community bank can browse possible refinancing conversations, compare lenders, and explore illustrative savings. Every included business, bank, address, and loan is fictional and marked `SYNTHETIC`.

## Run locally

Use Node.js 20.9 or newer and npm. From the repository root:

```sh
cd demo
npm ci
cp .env.example .env.local
npm run dev
```

Open [localhost:3000](http://localhost:3000) to go directly to the dashboard. No access code, SBA download, or API key is required to browse. Browse the refinancing and watch lists, change filters, or adjust the illustrative bank rate.

For a production build:

```sh
npm run lint
npm run build
npm start
```

The prospect brief button calls OpenRouter only when used. To enable it, set your own `OPENROUTER_API_KEY` in `.env.local`, restart the server, and expect a paid external model request. Without a key the button reports that the API is not configured; the dashboard still works. Browser navigation and build verification do not verify live model responses.

## Environment

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY` | Server-only OpenRouter credential for optional AI briefs. |

The dashboard opens directly with no authentication. Static JSON reaches the browser and the brief API has no server authentication or rate limiting. Keep this a local synthetic demonstration; do not put customer data into it or expose a funded API endpoint without separately implementing deployment controls.

## Data and limitations

`data/prospects.json` contains six invented records, three refinancing candidates and three recent-loan watch records. Counties describe the example market; all business names, addresses, ZIPs, amounts, dates, rates, and outcomes are fabricated. Savings are simple annual interest illustrations, not amortization schedules or credit decisions. Variable rates and fees can change any real comparison.

The dashboard reads static JSON and does not invoke the root Python report pipeline. The optional `../scripts/build_demo_data.py` reads local `../data/foia-7a-*.csv` files, optionally uses `FRED_API_KEY` or `PRIME_RATE_OVERRIDE`, and **overwrites** this synthetic JSON with real public loan records. Its output should remain private and must not be committed. See the root README for the download source and generator environment settings.
