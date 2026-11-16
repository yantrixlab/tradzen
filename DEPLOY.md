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
**The easy way: users download it from your site.** The server packages the extension for its own address and offers it as `https://signals.example.com/download/extension.zip`: a **Download the Chrome extension** button on the public landing page, a **Get extension** button in the top bar of the admin panel, and a card with the install steps on **My account**. It needs nothing from you except a redeploy (the Docker image contains the `extension/` folder). The address baked into the file is `QXS_PUBLIC_URL`, else `https://QXS_DOMAIN`, else the https address of the request itself; on a server without https the buttons are simply not shown. The download is public (it holds no secrets; signing in still needs an account).

**By hand** (for a Chrome Web Store upload, or to send the file yourself):
```bash
python3 deploy/package_extension.py https://signals.example.com
```
makes `dist/tradzen-extension-signals.example.com.zip`. Users unzip it, open `chrome://extensions`, switch on Developer mode, **Load unpacked**, then click the extension, open **Connection**, sign in with their Tradzen username and password (an access token from *My account* still works too), and reload their Quotex tab. The first Quotex account the extension sees is tied to that Tradzen account (`QXS_QUOTEX_LINK`); an admin can unlink it on the Users page. (To avoid developer mode you can publish the zip as an unlisted item on the Chrome Web Store; review rules apply.)

## Shared candles
Only the price-feed tab builds candles from live ticks, but every signed-in extension also sees the chart history its own Quotex page loads (when a pair is opened or its chart is scrolled back). Those candles are offered to the server, which keeps **one candle store for everybody**:
- **First write wins.** A candle the server already has is never changed by a contribution; if the stored one disagrees it is only counted (Data → Shared candles → Disagreements). The price feed's own live candle may still replace a contributed one.
- **Checked before storing:** a known pair, a stored timeframe, a finished candle, sane open/high/low/close, no jumps between neighbours, a price level close to what is stored. A candle that fails is counted, not stored.
- **Load control:** each account gets a small allowance (12 history items a minute, about 21,000 candles an hour) and the whole server a write budget; beyond them the answer is "busy, retry in N seconds" and nothing is queued. The extension remembers which ranges the server answered for and does not send them again.
- **Trust:** an account whose candles are mostly refused or in conflict is paused automatically; the admin can pause, resume or remove (all candles that account contributed, tagged `contrib:<id>`) at any time. Only the Quotex account linked to the Tradzen account contributes.
- **Gaps:** the extension shows the ranges the server wants (recent holes of streaming pairs first), so a user can open the pair and scroll back. Candles that close a hole the feed left are used by the engine at once.
- Switch it off with `QXS_CONTRIB=off`, or per plan with the *Share candles* feature. Contributions do not add live prices: only the price feed streams ticks.

## Live prices from ordinary accounts
Only pairs open in the price-feed tab used to produce live signals. Now any signed-in extension can also stream the live prices of the pairs open in **its** Quotex tab, under these rules (Data -> Live feed shows it all):
- **One source per pair (a lease).** Exactly one account holds a pair at a time; everybody else's prices for it are only compared with the held ones, never applied. The extension only says which pairs it sees; the server answers which ones to send (held ones, free ones, and a 15-second spot-check every couple of minutes on a held pair), so bandwidth goes only where it is needed.
- **The admin's price feed always has priority.** While it streams a pair (a tick within 8 s) no lease exists on it, and a holder steps aside the moment it returns. A holder silent for 5 s loses the pair and the next account that offers ticks takes it. The engine already discards a candle that has a hole in its ticks, so a hand-over can lose one candle but cannot make a wrong one.
- **Every price is checked:** finite and positive, not older than 30 s, not in the future, newer than the last applied one (a replay or a second tab is "late", not an offence), within 2 % of the previous price (or, after a pause, within 10 % of the last stored candle). Five refused prices in a row cost the lease.
- **Load control:** 40 prices a second per account, a server-wide budget, at most 8 pairs per account, at most 300 prices looked at per request; beyond that the extra is dropped and the answer says "busy".
- **Trust:** an account that is mostly refused (more than 30 % after 200 prices), or whose spot-check prices mostly disagree with the applied ones (more than 50 % after 60), is paused automatically. The admin can pause, resume or **remove** one account's data: candles built from an account's prices are tagged `tick:<id>`, so they can be deleted as a whole (candles built across a hand-over, or by the feed, are not touched).
- **Only the linked Quotex account** feeds; switch it off with `QXS_LIVE_FEED=off`, or per plan with the *Share live prices* feature.
- **Limits that remain:** a pair is only as good as its holder. The spot-checks and limits catch jumps, wrong levels and disagreement, not a consistently wrong but smooth price. Keep your own price-feed tab running for the pairs that matter.

## Live sessions
The admin runs a **session** (Session console in the admin panel, `/admin/session`): start it, send signals by hand, end it. Everybody whose plan includes **Live sessions** gets its chart and alerts automatically (`/admin/sessions`, the extension's cards and sounds, and the extension's side panel for ordinary accounts).
- **Three steps per signal:** a *heads-up* (the candle and the price area are marked on every viewer's chart, nothing to trade yet), the *signal* (take it on the entry candle) and the *result* (settled by that candle: its open against the close of its last candle, the same rule as the strategy signals; a candle that never arrives voids it). The admin can cancel a heads-up quietly, or a sent signal (everybody is told not to take it).
- **On the chart:** an arrow on the candle the trade comes after, the entry candle shaded by state (heads-up blue, running purple, win green, loss red), and the area as a box that runs to the right edge until the trade has ended.
- **The chart follows the admin's own Quotex tab**; picking a pair on the console holds it, "Follow my Quotex tab again" hands control back. One live session at a time; it survives a restart and ends by itself after 12 hours.
- **Tracking:** every session keeps its signals, the win / loss / draw / void count, the accuracy with its 95 % range against break-even, and the net in units of the stake at the payout stored when the signal was sent (the session history, also for viewers).
- **Free and premium:** a session is free or premium. A premium session's signals, alerts and results are shown only to plans with the feature **Premium sessions** (off by default: make a "Premium" plan on the Features page, or give one user the feature). This is enforced on the server; the shared candle chart itself stays visible. Each user can also switch session alerts off on the Live sessions page.
- Alerts reach users while their extension is signed in and the Quotex tab is open; the Live sessions page also plays a tone and shows a banner. There is no phone or Telegram push yet.

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
- **Hosted side panel:** the extension's side panel shows the live board (admin accounts). It signs in by itself: the extension trades its access token for a one-time ticket (`POST /api/auth/panel-ticket`, valid a minute), the page inside the panel trades the ticket for a limited 12-hour session that can only use the live board's routes (`panel` scope, `PANEL_ALLOW` in `auth.py`), sent as a header, not a cookie. Only `/admin/...?panel=1` may be framed, and only by an extension page. If the session ends the panel reloads itself with a new ticket. What the panel shows: an admin gets the full Quotex Signal page; everybody else gets **Live chart** (`/admin/chart`), the chart of the pair open on *their own* Quotex tab. The extension reports that pair (`sel`) with every account's batches, the server keeps one pair per account (`/api/mychart`), and the chart switches by itself when they switch pairs on Quotex, until they pick a pair or timeframe by hand ("Follow my Quotex tab again" hands control back). Switch it off per plan with the *Live chart* feature.
- **Not tested here:** the Docker files were written without a Docker engine on the development machine. Run steps 3 to 5 on a throwaway server first and report anything that does not start.

## Before you open it to the public
Signal services for binary options are restricted or banned in some countries, reading the broker's pages may breach its terms, and nothing in the app is financial advice or a promise of profit (its own records show the measured edges are small or unproven). Check the rules that apply to you and to your users first.
