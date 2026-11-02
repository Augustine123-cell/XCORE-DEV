# 𝚇𝙲𝙾𝚁𝙴® — WhatsApp Developer Bot

Developer: **𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽** • Built using SHΔDOWBORNE LΔB 𝚇𝙲𝙾𝚁𝙴®.

## Quick start
Requires **Python 3.10+**.
```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then set TELEGRAM_BOT_TOKEN and BAN_CHECKER_API_KEY
python -m xcore.preflight  # checks config and integration methods
python -m pytest -q
python -m xcore
```
On first start the bot requests a **WhatsApp pairing code** for `OWNER_NUMBER`. It is printed in the console and sent to the configured Telegram owners. You can also use `/pair` in the Telegram dashboard to connect a WhatsApp number. The bot displays the real WhatsApp-generated code with a `𝚇𝙲𝙾𝚁𝙴®` label; enter the actual code in WhatsApp → *Linked devices → Link with phone number*.
If a code can't be requested, the WhatsApp library prints a QR code in the console instead.

Optional force-join can be enabled by setting `FORCE_JOIN_URL_1`, `FORCE_JOIN_URL_2`, and/or `FORCE_JOIN_URL_3`. Leave them blank to disable it. Membership checks require public `t.me/<username>` links or numeric Telegram chat IDs; the bot should be an administrator in each channel/group for reliable checks. Configure up to two WhatsApp owners with `OWNER_NUMBERS` and up to two Telegram dashboard owners with `OWNER_TELEGRAM_IDS` (comma-separated). `TELEGRAM_ADMIN_ID` remains the fallback primary owner ID.

Shortcuts: `./start.sh` (Linux/macOS), `start.bat` (Windows), `bash termux-install.sh` (Termux),
`docker compose up -d`, `pm2 start ecosystem.config.js`, or the `Procfile` / `railway.json` / `nixpacks.toml` / `.replit` for hosts.
Hosted platforms don't read a bundled `.env` — set the variables in the host's dashboard.

## Persistent storage (surviving redeploys)
Everything the bot remembers lives in two places: `db.json` + `clans.json` (data dir) and the WhatsApp login keys (`sessions/`).
A container's own filesystem is wiped on every redeploy, so both must sit on a **volume**.

**Railway (one-time setup):**
1. Service → **Settings → Volumes → New Volume**, mount path `/data`.
2. Redeploy. That's it — the bot detects `RAILWAY_VOLUME_MOUNT_PATH` and uses `/data/data` and `/data/sessions`
   (Railway allows one volume per service, so both share it). Don't set `DATA_DIR`/`SESSION_DIR` unless you need to.
3. Check the deploy log for `✓ Persistent storage OK`. If you see `⚠ No Railway volume detected`, data is still ephemeral.
4. Pair WhatsApp **once more** after the first volume deploy; later redeploys reuse the saved session.

**Docker Compose:** already bind-mounts `./data`, `./sessions`, `./logs`. **Other hosts:** set `PERSIST_DIR` (or `DATA_DIR` + `SESSION_DIR`) to a durable folder.
Bundled defaults (`xcore/seed/clans.json`) are copied into the data dir on first start and never overwrite your edits.
`db.json` is written atomically with a rolling `db.json.bak`; if it ever becomes unreadable it is set aside as `db.json.corrupt-<time>` and the backup is restored.

## Layout
```
xcore/
  app.py            entrypoint: health server + Telegram + WhatsApp + monitor, graceful shutdown
  config.py         env → typed Config          paths.py   data/session dir resolution
  db.py             JSON database (same schema as the Node bot)
  clans.py          clan-tag database           format.py  menu & cards
  api.py            ban-check API client        urlguard.py SSRF guard
  monitor.py        uptime monitor              server.py  GET / health JSON
  telegram_bot.py   admin bot (python-telegram-bot)
  osint/            optional provider clients (fail closed until configured)
  whatsapp/
    socket.py       ← the ONLY file that talks to the WhatsApp library (neonize)
    handler.py      mode / role gating and dispatch
    context.py      Incoming / Ctx objects commands work with
    commands/       general.py  admin.py  apify.py  devtools.py  osint.py   (@command("name") registry)
tests/              python -m unittest discover -s tests -t .   (isolated from your real .env / env vars)
```
Adding a command: write `@command("name") async def run(ctx): await ctx.reply("…")` in any `commands/*.py`.

## Commands
Core commands include `®xcore`, `®botinfo`, `®ping`, `®get-id`, `®device`, `®clientid`, `®clientcheck`, `®identify`, `®session`, `®stats`, `®api`, `®uptime`, `®support`, `®buypremium`, `®checkaccess`, `®bancheck`,
`®health`, `®services`, `®watch`, `®apilab`, `®api-test`, `®scan`, `®monitor`, `®gitclone`, `®mediafire`, `®save contact`, `®readqr`, `®expel`, `®mode`, `®self`, `®public`, `®antiban`, `®antibug`,
owner: `®sudo`, `®delsudo`, `®premium`, `®ban`, `®localban`, `®unban`, `®remove`, `®restore`, `®banned`, `®restart`; group admins: `®ordain`, `®strip`, `®jid`, `®sweepclean`, `®purge`. premium OSINT provider stubs (`®wafind` …).

## Differences from the Node version
- **WhatsApp library:** Baileys (Node) → [neonize](https://github.com/krypton-byte/neonize) (whatsmeow). Sessions are stored in
  `sessions/<number>/session.sqlite3` — **existing Baileys sessions cannot be reused; re-pair once.** `db.json` and `clans.json` carry over unchanged (copy them into the data dir / volume).
- **Fixed bugs:** `data/clans.json` contained invalid JSON (`"members": sole,`) so the clan DB never loaded; the "service recovered" alert
  always reported 0s downtime; `.region` only recognised 3-digit country codes; `.restore` was in the menu but didn't exist.
- **Hardening:** URL guard now blocks every non-public address (incl. CGNAT and IPv4-mapped IPv6); DB writes are atomic;
  `.premium`/`.ban` validate their arguments. `.restart` exits and relies on your supervisor (Docker/PM2/Railway) to relaunch.
- Messages sent from the bot's own account now use that account as the sender (Baileys used the chat partner in DMs).
- WhatsApp may identify senders by anonymous "LID" ids; the adapter prefers the real phone-number JID when WhatsApp supplies it.
  Replying to someone to use `®sudo`/`®scan` relies on that; otherwise pass the number: `®sudo 23480…`.

## Security
Rotate `TELEGRAM_BOT_TOKEN` / `BAN_CHECKER_API_KEY` if you ever deployed the old v5.0.0 zip (it bundled a real `.env`).


## XCORE branding and prefixes

The WhatsApp command prefixes are `®` (primary) and `@` (alternative); the legacy dot prefix is disabled. Set `COMMAND_PREFIXES=®,@` to configure them. Telegram dashboard replies and WhatsApp command responses carry the XCORE identity and the developer credit `𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽`.


## XCORE group administration and anti-flood guard

Commands: `®ordain`, `®strip`, `®ban` (inside groups), `®jid`, `®purge` (owner only), and `®sweepclean` (owner-only preview + confirmation). All commands also accept `@`. `®ban` in a private chat retains local bot-access ban behaviour; use `®localban` to make that explicit.

The anti-flood guard detects repeated-message and burst patterns in groups. At threshold it attempts to revoke the recent suspicious messages and applies a 30-minute **bot-enforced soft mute** only when at least one message was successfully deleted; further messages from that sender are deleted while the bot is online and has permission to delete them. WhatsApp does not expose a reliable per-member native mute operation for ordinary group bots, so this is not a platform-level mute.

Device/client lookups only report metadata exposed by the linked WhatsApp library; they do not infer a device OS when WhatsApp does not expose it.


## Telegram Stars subscriptions

The Telegram dashboard supports `/buy` using Telegram Stars. Set `PREMIUM_30D_STARS` and/or `PREMIUM_365D_STARS` to positive integer Star prices to enable those plans; leave them at `0` to disable sales. A user must link a WhatsApp number with `/pair`; payments made before pairing are held against the Telegram user ID and activated after pairing. Owner controls: `/grantpremium <telegram_user_id> <whatsapp_number> <days>` (or linked-user shorthand `/grantpremium <telegram_user_id> <days>`), `/revokepremium <telegram_user_id>`, `/sudoadd <whatsapp_number>`, and `/sudoremove <whatsapp_number>`.

## Important deployment limitations

- Add the linked WhatsApp account as a group admin in every group where moderation is expected. `®ordain`, `®strip`, `®ban`, `®purge`, and `®sweepclean` depend on WhatsApp group permissions.
- `®sweepclean` is a two-step operation: preview, then `®sweepclean confirm` within 60 seconds. It retains the bot, registered XCORE owners, group admins, and the group creator.
- **Anti-Bug engine (v2).** Every inbound message (DMs and groups) is scored 0-100 for *bug strength* from payload signals (oversized text, control / invisible / bidi / combining-mark floods, giant tokens, mass mentions, oversized protobuf, oversized contact cards, payloads that fail to parse) and behaviour signals (flood, duplicates). At or above the threshold (default 60) the bot counters: it blocks the sender on WhatsApp, deletes every message it saw from them, adds them to a bot-level shield that drops everything they send, and reports to the owner with the strength and a **counter effectiveness %**. Effectiveness is calculated only from confirmed actions (block 50 · purge 30 · shield 20), so a failed or unsupported step lowers the number instead of being hidden. Owner, SUDO, the linked number and `®antibug trust` numbers are never countered. Controls: `®antibug status | on | off | block on/off | purge on/off | level low/normal/high | trust N | untrust N | unblock N | list`.
- Limits: WhatsApp cannot delete *another person's* DM messages for everyone. In DMs the purge uses delete-for-me / chat wipe where the installed `neonize` exposes it; in groups it needs the bot to be a group admin. Whatever could not be done is shown in the alert and lowers the effectiveness figure.
- Groups also keep the 30-minute soft mute: when the bot is a group admin it deletes that sender's later messages while it is online.
- Device OS, client metadata, and account-ban status are reported only when the connected library or configured private API supplies them.
- Telegram Stars prices are not configured by default. Set the two `PREMIUM_*_STARS` environment variables before expecting `/buy` to sell plans.

See `DEPLOYMENT_CHECKLIST.md` for the required environment variables, Railway volume setup, preflight command, and safe live-smoke-test order.


## 𝚇𝙲𝙾𝚁𝙴® Telegram owner dashboard

The owner Telegram account can open `/admin` (or `/start`) to access the inline control panel. It provides overview statistics, paired-account listing with per-account unpair confirmation buttons, process RAM reporting, and shortcuts for owner controls. Owner commands:

- `/pairnumber +2348012345678` — request a WhatsApp linking code for another account.
- `/unpair 2348012345678` — disconnect an active session, remove its local session credentials, and remove it from the paired registry. This does not revoke other linked devices from the WhatsApp mobile app; use WhatsApp → Linked devices for that.
- `/paired` — list registered paired numbers and their linked Telegram IDs.
- `/ram` — show process RAM use (uses psutil if installed, otherwise Linux `/proc`).
- `/grantpremium <telegram_id> <whatsapp_number> <days>` — link the Telegram identity to the number and grant Premium.
- `/grantpremium <telegram_id> <days>` — grant Premium to a Telegram user already linked by `/pair`.

Every WhatsApp outgoing message and Telegram bot response is wrapped with the official XCORE header and the `𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽` developer credit.

## Apify integration

XCORE includes an Apify integration for public WhatsApp invite discovery and allowlisted generic Actor runs. Configure these Railway variables:

```env
APIFY_TOKEN=your_secret_apify_token
APIFY_USER_ID=your_apify_user_id
APIFY_ACTORS=groups=lofomachines~whatsapp-group-search
```

`APIFY_USER_ID` is informational; API authentication uses the secret token. Keep the token private. To add more Actors, append comma-separated aliases, for example `groups=lofomachines~whatsapp-group-search,places=yourname~your-places-actor`. Only configured aliases can be run from XCORE.

WhatsApp commands:
- `®groups <keyword> [country-code] [max-results]` — search public WhatsApp invite links (default country NG, default limit 10; maximum 100). Premium, SUDO, or owner access is required.
- `®apify actors` — list configured Actor aliases (SUDO/owner).
- `®apify me` — verify the Apify token and show account identity (SUDO/owner).
- `®apify run <alias> <JSON-input>` — run a configured Actor (SUDO/owner).

Actor runs can incur Apify usage charges. The group-search Actor only discovers public invite links and public preview metadata; it does not join groups, read private messages, or retrieve member lists. The Actor's free tier may cap results at 20 per run.


## Final utility commands

- `®gitclone <HTTPS URL>` — SUDO/owner only; shallow-clones public GitHub, GitLab, or Bitbucket repositories into `data/cloned_repositories`. The source is downloaded only and is never executed by XCORE. Git must be installed (included in the Docker and Nixpacks configs).
- `®mediafire <public link>` — SUDO/owner only; resolves a public MediaFire file, downloads it with a 50 MB cap, and sends it to the current chat as a document. Private/challenge-protected links may not work.
- `®save contact <tag> <international-number>` — sends a WhatsApp contact card. Alternatively reply to a user's message and run `®save contact <tag>`. The recipient chooses whether to save the contact.
- `®readqr` — reply to an image containing a QR code. XCORE decodes and displays the contents as text; it never opens decoded links. Requires Pillow and zxing-cpp (installed from requirements).
- `®expel` — group admin command; reply to a member's message (or provide their number). The bot account must be a group admin. Group creators, XCORE owner numbers and the bot account are protected.

Git clone and MediaFire downloads are restricted to the owner/SUDO role. Contact and QR utilities are available to users permitted by the current bot mode. The redesigned `®xcore` lists the commands by category, and restricted commands display the XCORE access-denied card. Existing dev/stealth/maintenance modes retain their configured silent behavior.


## XCORE startup branding assets

The WhatsApp `®xcore` command sends the bundled XCORE brand image, a timestamped identity block, the bundled soundtrack, and then the normal command menu. The legacy `®menu` command remains available for backward compatibility. The Telegram `/start` flow sends the brand image after force-join verification and does not send the soundtrack. Assets are bundled under `xcore/assets/`.
