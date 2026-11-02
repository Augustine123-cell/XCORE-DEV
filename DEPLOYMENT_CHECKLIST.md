# 𝚇𝙲𝙾𝚁𝙴® — Deployment Checklist

Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽  
Built using SHΔDOWBORNE LΔB 𝚇𝙲𝙾𝚁𝙴®

## 1. Required environment variables

Copy `.env.example` to `.env` for a VPS, or add the same values in the Railway service Variables panel. Never upload a real `.env` file to GitHub.

- `TELEGRAM_BOT_TOKEN`: token from BotFather.
- `TELEGRAM_ADMIN_ID`: your numeric Telegram user ID.
- `OWNER_TELEGRAM_IDS`: up to two owner Telegram IDs, comma-separated.
- `OWNER_NUMBER`: primary WhatsApp owner number, international digits only.
- `OWNER_NUMBERS`: both owner WhatsApp numbers, comma-separated, international digits only.
- `BAN_CHECKER_API_URL`: your private ban-check API endpoint.
- `BAN_CHECKER_API_KEY`: private API key.
- `BAN_CHECK_API_KEY_HEADER`, `BAN_CHECK_API_KEY_PREFIX`, `BAN_CHECK_HTTP_METHOD`, `BAN_CHECK_PHONE_FIELD`: match your provider's contract.
- `BOT_MODE=public`: needed for normal users and paid users to access public commands.
- `COMMAND_PREFIXES=®,@`: primary and alternative WhatsApp prefixes.
- `PREMIUM_30D_STARS` / `PREMIUM_365D_STARS`: optional Telegram Stars prices. Both default to `0` (sales disabled). Do not enable sales until the Premium benefits and prices are final.

Optional: `FORCE_JOIN_URL_1` through `_3`, `PERSIST_DIR`, `DATA_DIR`, `SESSION_DIR`, and `PORT`.

## 2. Preflight and launch

```bash
python -m pip install -r requirements.txt
python -m xcore.preflight
python -m pytest -q
python -m xcore
```

A successful preflight checks configuration and the installed Neonize/Telegram integration surfaces. It does not pair a real WhatsApp account or prove that WhatsApp will accept group actions.

## 3. Persistent storage

Persist both the database directory and WhatsApp session directory. On Railway, attach a Volume mounted at `/data`; the project detects `RAILWAY_VOLUME_MOUNT_PATH`. Do not run multiple copies of the same WhatsApp session simultaneously.

## 4. First live test

1. Start the service and pair the primary owner WhatsApp number using the code sent to the Telegram owner dashboard (or the console QR fallback).
2. Confirm `/status` in Telegram and `®ping` / `@botinfo` in WhatsApp.
3. Test `®bancheck <number>` against your private API with a number you are authorised to check.
4. Add the linked WhatsApp account as an administrator in a disposable test group.
5. Test `®jid`, `®ordain`, `®strip`, and group `®ban` with test accounts.
6. Test `®sweepclean` only in a disposable group: first run previews; `®sweepclean confirm` executes within 60 seconds. It protects the bot, configured owners, group admins, and the group creator.
7. Test `®purge` only in a disposable group. It is owner-only and demotes eligible admins, never the group creator, bot, or configured owners.
8. Test antibug with a SECOND phone number you control (never your own): send it a 20 KB+ text to the bot's number, or 10 quick messages. Expected: the second number is blocked on WhatsApp, the owner receives a "THREAT COUNTERED" alert with strength and effectiveness %, and `®antibug list` shows the number. Then run `®antibug unblock <number>`. In a disposable group, the bot must be a group admin to delete messages (7 quick messages triggers it).
8b. Check the alert's "blocked on WhatsApp" line. If it says "not supported here", your installed `neonize` version exposes a different block API — send me the output of `pip show neonize` and the alert text.

## 5. Behaviour and platform limits

- Antibug's 30-minute mute is a bot-enforced soft mute: subsequent messages are deleted while the bot is online and still has group delete permissions. It is not a WhatsApp-native per-member mute. If deletion cannot be performed, the bot logs and alerts but does not claim that a mute was enforced.
- `®device` / `®clientid` show only device/client metadata returned by WhatsApp. Android/iOS is not guessed when unavailable.
- `®clientcheck` uses WhatsApp's registration check; it does not reveal private client internals.
- Group commands require the connected WhatsApp account to be a group admin. The bot owner's identity does not bypass WhatsApp permissions.
- `®sweepclean` keeps administrators by default. It removes eligible non-admin members after explicit confirmation.
- Telegram Stars checkout is implemented but disabled until positive Star prices are configured. Provider-backed OSINT commands from the inherited project remain unavailable unless their providers are configured; do not sell Premium as a product until its actual benefits are defined and verified.
- The project has automated tests, but a real deployment still needs live Telegram payment, WhatsApp pairing, private API, and group-permission smoke tests.


## Final utility smoke tests

After the bot is connected, test these in a private chat/group where the relevant permissions apply:

- `®menu` — confirm the redesigned command sections and access badge display.
- `®gitclone https://github.com/octocat/Hello-World` — SUDO/owner only; confirm the repository is saved under the configured data directory. Do not execute downloaded source.
- `®mediafire <public-link>` — SUDO/owner only; test with a small file first; it should be delivered as a WhatsApp document.
- `®save contact XCORE TEST 2348012345678` — confirm a native contact card is sent.
- Reply to a QR image with `®readqr` — confirm decoded text is returned without opening links.
- Reply to a non-admin member with `®expel` — only test in a group where the connected account is an admin and you have authority to remove that member.

MediaFire resolution depends on the public file page format and may fail for private, expired, or challenge-protected links. WhatsApp may reject documents above its own media limits even when the download is below XCORE's 50 MB cap.
