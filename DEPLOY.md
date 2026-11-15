# Putting Tradzen on a VPS

> **Using Coolify?** Follow `COOLIFY.md` instead: Coolify supplies the proxy and HTTPS, so only the Dockerfile is used. This file is for a plain VPS with Docker Compose + Caddy.

One small server runs everything: the backend (the market feed, strategies, users) and Caddy, which gets and renews the HTTPS certificate for your domain. Everything is configured in one `.env` file.

## What you need
- A VPS with 2 GB RAM or more (Ubuntu 22.04 / 24.04 is what these steps assume) and a domain name whose DNS A (and AAAA) record points at it.
- Ports 80 and 443 open to the internet. Keep port 8787 closed: the backend is only reachable through Caddy.
- Your own Quotex tab, open all the time, in a browser with the extension: that tab is the **price feed** for every user (see "The price feed").

## 1. Install Docker
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # log out and in again
```
Optional firewall (check that your SSH port is allowed before enabling it):
```bash
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
```

## 2. Get the code and the settings
Copy the project folder to the server (git clone, or `rsync -a --exclude node_modules --exclude .venv --exclude backend/data ./ user@server:tradzen/`), then:
```bash
cd tradzen
./deploy/prepare.sh signals.example.com admin     # writes .env with a random secret key and a random admin password (printed once)
nano .env                                          # optional: backups, background jobs, memory limit
```
Without the script: `cp .env.example .env` and fill in `QXS_DOMAIN`, `QXS_ADMIN_USER`, `QXS_ADMIN_PASSWORD` (10+ characters) and `QXS_SECRET_KEY`. The server refuses to start with a weak admin password or no secret key.

## 2b. Bring the data you already collected
The first start can load everything your local installation has gathered (candles of every pair, the signal history, strategies, backtests, your own trades and the AI's files) so the server does not begin empty. On your own computer, from the `backend` folder:
```powershell
.venv\Scripts\python -m app.seed make          # writes ..\seed\tradzen-seed.tar.gz (about 30 MB for 700,000 candles)
```
This takes a consistent snapshot, so the local server can keep running. Then copy it to the server before the first start:
```bash
scp seed/tradzen-seed.tar.gz user@server:tradzen/seed/      # the folder is mounted into the container
```
On the first start (no database yet) the server unpacks it as its database and logs `loaded the seed: candles 724,837, ...`. After that the file is ignored, so it can stay or be deleted. The data you had locally belongs to the admin account from `.env`.

Things to know:
- It works **only on the first start**. A seed holds one whole database, so it cannot be mixed into a server that already has users and strategies. (Starting again from a seed means deleting the server's data volume, which also deletes its users.)
- **Top-ups later:** keep collecting locally and add the newer candles to a running server whenever you like. Rows the server already has are kept; accounts, strategies and signals are never touched:
  ```bash
  scp seed/tradzen-seed.tar.gz user@server:tradzen/seed/
  docker compose stop backend
  docker compose run --rm backend python -m app.seed merge /seed/tradzen-seed.tar.gz
  docker compose start backend
  ```
  (Stopping the backend first is the safest; the merge works in small steps, one pair at a time, if you do it while it runs.)
- The seed contains **your own trades and settings** too. Do not share the file.
- A seed from a newer version of the app than the server's is refused with a clear message: update the server first.

## 3. Start it
```bash
docker compose up -d --build
docker compose logs -f backend          # should say: Tradzen: PRODUCTION mode, sign-in ON ...
```
Open `https://signals.example.com` and sign in with the admin from `.env`. The first start also creates the database; Caddy needs a minute to get the certificate.

## 4. Set it up (all in the admin panel)
1. **Users**: create accounts (no public sign-up). Roles: Admin (everything), Editor (strategies, backtests, signals), Trader (a normal user). A new user gets a generated password and must choose their own at the first sign-in.
2. **Features**: switch menus and capabilities on or off per plan, set limits (own strategies, backtests per day). New users get the default plan "Standard". On the Users page you can give one user another plan, an end date, or single overrides.
3. **Strategies**: build and test strategies as the admin or an editor. A shared strategy goes *Testing* (staff only) -> *Library* (users add it) -> *Everybody*, set with the dropdown on the Strategies page.
4. **My account -> Chrome extension**: create a **price feed** token for your own browser (admin only). Everyone else creates an ordinary token for their own browser.

## The price feed
Prices come from the Quotex tab of the account that holds the *price feed* token. If that tab or browser stops, nobody gets signals. Use a computer that stays on. Only pairs open (streaming) in that tab produce live signals. Ordinary users' extensions only send their own trades and balance and receive alerts.

## 5. The extension for your users
```bash
python3 deploy/package_extension.py https://signals.example.com
```
makes `dist/tradzen-extension-signals.example.com.zip`. Users unzip it, open `chrome://extensions`, switch on Developer mode, **Load unpacked**, then click the extension, open **Connection**, sign in with their Tradzen username and password (an access token from *My account* still works too), and reload their Quotex tab. The first Quotex account the extension sees is tied to that Tradzen account (`QXS_QUOTEX_LINK`); an admin can unlink it on the Users page. (To avoid developer mode you can publish the zip as an unlisted item on the Chrome Web Store; review rules apply.)

## Updating
```bash
git pull            # or copy the new files
docker compose up -d --build
```
The database is migrated automatically at start. The data lives in the Docker volume `qxs-data`, so rebuilding does not touch it.

## Backups
A nightly backup of the main database and every user's private database is kept in the data volume (`backups/`, newest 7; `QXS_BACKUP*` in `.env`). Admins can also make one at any time (`POST /api/system/backup`) and list them (`GET /api/system/backups`). **A backup on the same disk does not protect against losing the server**: copy them off it regularly, for example:
```bash
docker compose cp backend:/app/backend/data/backups ./backups-copy
```
To restore: `docker compose stop backend`, copy the files of one backup folder over `signals.db` and `users/*.db` inside the volume, `docker compose start backend`.

## Good to know
- **One process.** The live market is held in memory, so the backend runs a single worker. It handles hundreds of users; beyond that the database would move to Postgres (not built yet).
- **Passwords.** Change the admin password by editing `QXS_ADMIN_PASSWORD` in `.env` and running `docker compose up -d`. Everybody else's password can be reset on the Users page.
- **Sign-in protection.** Five wrong passwords lock that address and username for 5 minutes; every admin action is in the audit log (Users page).
- **Logs:** `docker compose logs backend` / `caddy` (rotated, 10 MB x 5).
- **Backups** are on the System page (Backups card: the nightly schedule, the list, a *Back up now* button).
- **Hosted side panel:** the extension's side panel is not part of the hosted build (the page forbids being framed); users open the site in a tab.
- **Not tested here:** the Docker files were written without a Docker engine on the development machine. Run steps 3 to 5 on a throwaway server first and report anything that does not start.

## Before you open it to the public
Signal services for binary options are restricted or banned in some countries, reading the broker's pages may breach its terms, and nothing in the app is financial advice or a promise of profit (its own records show the measured edges are small or unproven). Check the rules that apply to you and to your users first.
