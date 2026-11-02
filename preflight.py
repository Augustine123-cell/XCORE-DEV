"""Deployment preflight: validate required configuration and installed integration surfaces."""
import importlib
import os
import sys


def main() -> int:
    print("╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮")
    print("DEPLOYMENT PREFLIGHT — Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽")
    errors = []
    warnings = []
    try:
        from . import config
        cfg = config.get()
        print(f"[OK] Configuration loaded | WhatsApp owners: {len(cfg.owner.numbers)} | Telegram owners: {len(cfg.telegram.owner_ids)}")
        print(f"[OK] Command prefixes: {' / '.join(cfg.prefixes)}")
        print(f"[INFO] Telegram Stars plans: 30d={cfg.telegram.premium_30d_stars} XTR, 365d={cfg.telegram.premium_365d_stars} XTR")
        if not cfg.telegram.premium_30d_stars and not cfg.telegram.premium_365d_stars:
            warnings.append("Telegram /buy is disabled until at least one PREMIUM_*_STARS price is set.")
        if cfg.bot.mode != "public":
            warnings.append("BOT_MODE is not public; ordinary paid users may be blocked from most commands.")
    except Exception as exc:
        errors.append(f"Configuration: {type(exc).__name__}: {exc}")
        cfg = None

    required = {
        "neonize": ["client", "events", "proto.waE2E.WAWebProtobufsE2E_pb2"],
        "telegram": ["ext"],
        "httpx": [],
        "dotenv": [],
    }
    for module, children in required.items():
        try:
            importlib.import_module(module)
            for child in children:
                importlib.import_module(f"{module}.{child}")
            print(f"[OK] Dependency: {module}")
        except Exception as exc:
            errors.append(f"Dependency {module}: {type(exc).__name__}: {exc}")

    try:
        from neonize.client import NewClient
        needed = ("get_group_info", "update_group_participants", "revoke_message", "get_user_devices", "is_on_whatsapp")
        missing = [name for name in needed if not callable(getattr(NewClient, name, None))]
        if missing:
            errors.append("Neonize missing required methods: " + ", ".join(missing))
        else:
            print("[OK] Neonize group, delete, device and client-check methods available")
    except Exception as exc:
        errors.append(f"Neonize API inspection: {type(exc).__name__}: {exc}")

    if cfg:
        if not os.path.isdir(str(cfg.paths.sessions)):
            warnings.append(f"Session directory will be created: {cfg.paths.sessions}")
        if not (os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("PERSIST_DIR") or os.getenv("DATA_DIR")):
            warnings.append("No persistent-volume environment detected; WhatsApp sessions may be lost on redeploy.")

    for warning in warnings:
        print("[WARN] " + warning)
    for error in errors:
        print("[FAIL] " + error)
    print("╰━━━━━━━━━━━━━━━━━━╯")
    print("Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
