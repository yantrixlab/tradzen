# Deploying Tradzen with Coolify

Coolify already runs its own reverse proxy and gets the HTTPS certificate, so you deploy **only the Tradzen container** (the `Dockerfile` in the repository root). You do not use `docker-compose.yml` or the Caddyfile; those are for a plain VPS without Coolify (see `DEPLOY.md`).

Menu names in Coolify change between versions; if a label below differs slightly, look for the nearest one.

## 1. Before you start
- Coolify is running on your VPS and can reach GitHub.
- A domain (for example `app.yourdomain.com`) with an **A record pointing at the VPS**.
- The code is in your GitHub repository (`yantrixlab/tradzen`). Never commit `.env`, `seed/` or `phase0/`: `.gitignore` already excludes them.
- The VPS needs about 2 GB of RAM to build (pandas, numpy and the admin build). On a smaller server add swap: `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`.
- A secret key and an admin password. Make the key on any computer:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(48))"
  ```

## 2. Create the application
1. **Project → your environment → + New Resource → Private Repository (with GitHub App)** (or *Public Repository* if the repo is public).
2. Pick `yantrixlab/tradzen`, branch `main`.
3. **Build Pack: Dockerfile.** Base directory `/`, Dockerfile location `/Dockerfile`.
4. **Ports Exposes: `8787`.**
5. **Domains:** `https://app.yourdomain.com` (Coolify makes the certificate itself).

## 3. Settings you must not skip
**Persistent Storage** - add a volume:
| Name | Destination path |
|---|---|
| `tradzen-data` | `/app/backend/data` |

Without it the database is thrown away at every deploy. This folder holds the main database, every user's private database, the nightly backups and the AI files.

**Environment Variables** (runtime, not build-time):
| Variable | Value |
|---|---|
| `QXS_ADMIN_USER` | your admin username |
| `QXS_ADMIN_PASSWORD` | 10+ characters, nothing common (the server refuses to start otherwise) |
| `QXS_SECRET_KEY` | the key you generated (16+ characters; changing it signs everybody out) |
| `QXS_CONTACT` | optional: `mailto:you@yourdomain.com` or an `https://` link; turns on the "Request access" button of the landing page |
| `QXS_BACKUP` / `QXS_BACKUP_HOUR` / `QXS_BACKUP_KEEP` | optional: `on` / `4` / `7` (nightly backups are already on in hosted mode) |

(`QXS_ENV=production` and `QXS_DB` are already set inside the image.)

**Health Check** (if you enable it in Coolify): path `/healthz`, port `8787`. The image also has its own health check.

## 4. Deploy
Press **Deploy** and open the logs. The first build takes several minutes. The server is ready when the log shows:
`Tradzen: PRODUCTION mode, sign-in ON ...`
Open `https://app.yourdomain.com`: visitors who are not signed in see the **landing page**; **Sign in** leads to `/admin`. Sign in with your admin from the variables.

## 5. Load your existing data (the 700,000 candles, history, strategies, your trades)
On your own computer, in the `backend` folder:
```powershell
.venv\Scripts\python -m app.seed make
```
This writes `seed\tradzen-seed.tar.gz` (about 30 MB) and can run while your local server keeps working. Then, on the site:
1. **System → Import data from another installation → choose the file.** It uploads (with a progress bar) and shows what is inside it next to what the server holds now.
2. **Load everything and restart.** This is allowed while nobody but the admin has an account, so do it **before** creating users. The server restarts to load it (about a minute); the page reloads by itself. The previous database is kept next to it on the volume (`signals.db.before-seed-<time>`).
3. If the container does not come back by itself, press **Restart** in Coolify (it has a restart policy by default, but check).

Later, to add newer candles without touching accounts or strategies, upload the new file and use **Add the candles to this server**.

The seed file contains your own trades: do not share it or commit it.

## 6. Connect your Quotex tab (the price feed)
1. On the site: **My account → Chrome extension**, tick **Price feed**, create the token, copy it (shown once).
2. On your computer, from the project root, build the extension for your domain:
   ```powershell
   backend\.venv\Scripts\python deploy\package_extension.py https://app.yourdomain.com
   ```
3. Unzip `dist\tradzen-extension-app.yourdomain.com.zip`, open `chrome://extensions`, turn on Developer mode, remove the old local copy, **Load unpacked**, click the extension, paste the token, **Save connection**, reload the Quotex tab.
4. The sidebar shows **live** within seconds. Your computer and that tab must stay on: they are the price feed for every user.

Other users create an ordinary token on their own account page and use the same extension zip.

## 7. Updating
Push to GitHub. In Coolify enable the **webhook / auto deploy** for the branch (or press Deploy). Data stays in the volume and the database migrates itself on start.

## 8. Backups
Backups run nightly into `/app/backend/data/backups` (System page → Backups, with a *Back up now* button). They live on the same disk as the data: copy them somewhere else now and then, for example from the Coolify **Terminal** of the application or with `scp` from the server's Docker volume folder.

## If something goes wrong
| Symptom | Likely cause |
|---|---|
| Certificate error / site not reachable | DNS not pointing at the VPS yet, or ports 80/443 closed in the provider's firewall |
| Container exits at start | Read the deployment log: weak admin password, missing `QXS_SECRET_KEY`, or a seed from a newer app version |
| Everything is empty after each deploy | The persistent volume for `/app/backend/data` is missing |
| "Load everything" is greyed out | Other users already have accounts on this server: use *Add the candles* instead |
| Page stays on "restarting" | The container was not restarted: press Restart in Coolify |
| Extension: HTTP 401 | The token is wrong or revoked: create a new one |
| No signals for anyone | The price-feed tab or computer is off, or no strategy is live yet |

## Before you open it to the public
Binary-options signal services are restricted or banned in some countries, reading the broker's pages may breach its terms, and nothing in the app is financial advice or a promise of profit. Check the rules that apply to you and to your users.
