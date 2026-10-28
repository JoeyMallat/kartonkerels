# Kartonkerels

Modular Flask + SQLite application, with separate page scripts and CSS. It runs as a Docker container on **Cloudflare Containers**. It is deployed automatically from GitHub and shares one database with the whole team.

## How the cloud setup works

```
browser ──https──> Cloudflare Worker (src/index.ts) ──> 1 Docker container (Flask + Waitress)
                                                            │
                                                Litestream streams every change
                                                            ▼
                                             Cloudflare R2 bucket (the database)
```

- A **single** container serves everyone (`max_instances: 1`), so all team members see the same data.
- The container disk is wiped whenever Cloudflare stops it, for example after 30 minutes without visitors or after a deploy. To survive this, [Litestream](https://litestream.io) copies every database change to an R2 bucket within about a second. On startup the container restores the latest copy before the app starts, so no data is lost. Member photos are stored inside the database, so they are covered too.
- The first visit after an idle period takes a few seconds while the container starts. Change `SLEEP_AFTER` in `wrangler.jsonc` (for example `"24h"`) to keep it awake longer; this costs a little more compute.

**Cost:** Containers require the **Workers Paid plan** ($5/month).
- The plan includes 25 GiB-hours of container memory per month. With the `basic` instance (1 GiB) that is about 25 awake hours.
- Beyond that, the container costs about $0.01 per awake hour. For example, 3 hours a day costs about $1/month extra, and 24/7 costs about $7/month extra.
- You only pay while the container is awake.
- R2 has a free tier of 10 GB, and this database is only a few MB.

## Deploy to Cloudflare (one-time setup)

1. **Push this folder to a GitHub repository.** It can be private. `.gitignore` already keeps the database, secrets and `node_modules` out of Git.
2. **Cloudflare account:** sign up at https://dash.cloudflare.com. Go to *Workers & Pages → Plans* and choose **Workers Paid**.
3. **Create the R2 bucket:** go to *R2 Object Storage → Create bucket* and name it `kartonkerels-data`.
4. **Create R2 access keys:**
   1. Go to *R2 Object Storage → Manage API tokens → Create API token*.
   2. Set the permission to **Object Read & Write** and limit it to the `kartonkerels-data` bucket.
   3. Copy the **Access Key ID** and **Secret Access Key**.
   4. Also note your **Account ID**, which is shown on the R2 overview page.
5. **(Optional) Bring your existing data:**
   1. Run `python export_for_cloud.py` on the PC that has your current `kartonkerels.db`. This creates `kartonkerels-cloud.db`.
   2. In the R2 dashboard, open the bucket, choose *Upload*, and upload that file into a folder named `seed`, renamed to `seed/kartonkerels.db`.
   3. Do this **before** the first deploy. The seed is only used when the bucket has no database yet.
6. **Connect GitHub:**
   1. Go to *Workers & Pages → Create → Import a repository* and select your repository.
   2. Use **`kartonkerels`** as the project name. It must match `name` in `wrangler.jsonc`.
   3. Leave the build command empty and keep the deploy command `npx wrangler deploy`. Cloudflare builds the Docker image for you, so you do not need Docker on your PC.
7. **Add secrets:** open the Worker, go to *Settings → Variables and Secrets*, and add each of these as type **Secret**:

   | Name | Value |
   |---|---|
   | `R2_ACCOUNT_ID` | your Cloudflare Account ID |
   | `R2_BUCKET` | `kartonkerels-data` |
   | `R2_ACCESS_KEY_ID` | from step 4 |
   | `R2_SECRET_ACCESS_KEY` | from step 4 |
   | `SECRET_KEY` | a long random string, e.g. the output of `python -c "import secrets;print(secrets.token_hex(32))"` |
   | `PKMNPRICES_API_KEY` | optional; the key can also be entered in the app under Settings |

   Then open *Deployments* and redeploy (or push a commit) so the container starts with these values.
8. **Open the site:** `https://kartonkerels.<your-subdomain>.workers.dev`. This is the free placeholder domain; the first start can take a few minutes after the very first deploy.
   - If you uploaded a seed, log in with your existing account.
   - Otherwise the first visitor creates the admin account. Do that yourself right away.

From now on, **every push to the main branch redeploys automatically**. Data stays in R2.

**Own domain later:** open the Worker, go to *Settings → Domains & Routes → Add → Custom domain*, and pick a domain that you manage in Cloudflare.

**Team accounts:** there is no public sign-up. The admin adds each member under *Settings → Team members*, and members then change their own password. Failed logins are limited to 10 per IP address per 15 minutes.

**Logs:** see *Workers & Pages → kartonkerels → Logs*, and the *Containers* page in the dashboard.

**Backups:** R2 holds the live replica. For an extra copy, download the objects in the bucket's `kartonkerels/` folder. You can restore that folder with `litestream restore`.

## Run locally (development)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open http://localhost:5000. Locally the database is `kartonkerels.db` in the project root. **Do not commit the database or API keys to Git.**

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `5000` (container: `0.0.0.0` / `8080`) | Listen address |
| `DATA_DIR` | project root (container: `/data`) | Folder for the database and the generated `.secret_key` |
| `DATABASE_PATH` | `DATA_DIR/kartonkerels.db` | Override database location |
| `SECRET_KEY` | generated once into `DATA_DIR/.secret_key` | Session signing key |
| `TRUST_PROXY` / `SECURE_COOKIES` | off (on in the cloud) | Behind Cloudflare / HTTPS only cookies |

## Structure

- `Dockerfile`, `deploy/`: container image, startup script (Litestream restore, replicate, first-start seed) and Litestream config
- `src/index.ts`, `wrangler.jsonc`, `package.json`: Cloudflare Worker that routes all traffic to the single container
- `export_for_cloud.py`: exports the local database for the first upload to R2
- `backup.py`: online SQLite backup copy for local use
- `app.py`: server entry point (Waitress), blueprint registration and `/health`
- `backend/core.py`: Flask configuration, database initialization, shared helpers, authentication guard, PkmnPrices client and matching utilities
- `backend/routes/catalog.py`: card catalog and TCGdex import
- `backend/routes/pricing.py`: automatic raw and PSA pricing
- `backend/routes/inventory.py`: inventory and CSV export
- `backend/routes/accounts.py`: login, members, settings, photos
- `backend/routes/operations.py`: batches, finance, watchlist, alerts and analytics
- `static/css/app.css`: shared styling
- `static/js/core.js`: shared browser state, routing, API calls and UI helpers
- `static/js/pages/`: individual page implementations
