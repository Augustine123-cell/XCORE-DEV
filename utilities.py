"""Safe utility commands for XCORE: repository clone, MediaFire, contacts and QR."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ... import config
from ...format import footer
from ...urlguard import UrlBlocked, assert_safe_url
from . import CommandError, command, require_sudo

MAX_MEDIA_BYTES = 50 * 1024 * 1024
GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}


def _clean_tag(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9 _.-]", "", value).strip()
    return value[:48] or "XCORE CONTACT"


def _number(value: str) -> str:
    return re.sub(r"\D", "", value or "")


@command("gitclone")
async def gitclone(ctx):
    require_sudo(ctx)
    if not ctx.args:
        raise CommandError("Usage: ®gitclone <https://github.com/owner/repository>")
    url = ctx.args[0].strip()
    try:
        parts = await assert_safe_url(url)
    except UrlBlocked as exc:
        raise CommandError(str(exc)) from None
    host = (parts.hostname or "").lower()
    if host not in GIT_HOSTS or parts.scheme != "https":
        raise CommandError("Only public HTTPS repositories on GitHub, GitLab or Bitbucket are supported.")
    if not shutil.which("git"):
        raise CommandError("Git is not installed on this host. Add Git to the deployment image and redeploy.")
    data_dir = config.get().paths.data
    target_root = data_dir / "cloned_repositories"
    target_root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", parts.path.strip("/").removesuffix(".git"))[-70:] or "repository"
    target = target_root / slug
    if target.exists():
        raise CommandError(f"A clone named '{slug}' already exists at {target}. Remove it before cloning again.")
    # No shell, no submodules, no hooks, no execution: this only downloads source files.
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", "--no-recurse-submodules", "--", url, str(target),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        shutil.rmtree(target, ignore_errors=True)
        raise CommandError("Git clone timed out after 120 seconds.") from None
    if proc.returncode:
        shutil.rmtree(target, ignore_errors=True)
        raise CommandError(f"Git clone failed: {err.decode(errors='replace')[-500:]}")
    await ctx.reply(f"✅ REPOSITORY CLONED\nRepository: {slug}\nLocation: {target}\nSource code was downloaded only; XCORE did not execute it.{footer(ctx.sender_number)}")


async def _mediafire_direct_url(page_url: str) -> str:
    try:
        parts = await assert_safe_url(page_url)
    except UrlBlocked as exc:
        raise CommandError(str(exc)) from None
    if parts.hostname not in {"mediafire.com", "www.mediafire.com"}:
        raise CommandError("Only public mediafire.com file links are supported.")
    async with httpx.AsyncClient(timeout=25, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 XCORE/1.0"}) as client:
        response = await client.get(page_url)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            direct = urljoin(page_url, location)
        else:
            html = response.text[:2_000_000]
            match = re.search(r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']|id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)', html, re.I)
            direct = next((x for x in match.groups() if x), "") if match else ""
    if not direct:
        raise CommandError("Could not resolve a direct MediaFire download. The file may be private, removed or protected by a challenge.")
    try:
        await assert_safe_url(direct)
    except UrlBlocked as exc:
        raise CommandError(f"MediaFire download blocked: {exc}") from None
    return direct


@command("mediafire")
async def mediafire(ctx):
    require_sudo(ctx)
    if not ctx.args:
        raise CommandError("Usage: ®mediafire <public MediaFire download link>")
    direct = await _mediafire_direct_url(ctx.args[0])
    data_dir = config.get().paths.data
    download_dir = data_dir / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlsplit(direct).path).name or "mediafire_download.bin"
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120]
    target = download_dir / name
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        async with client.stream("GET", direct) as response:
            response.raise_for_status()
            length = int(response.headers.get("content-length", "0") or 0)
            if length > MAX_MEDIA_BYTES:
                raise CommandError("Download exceeds the 50 MB safety limit.")
            total = 0
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > MAX_MEDIA_BYTES:
                        handle.close()
                        target.unlink(missing_ok=True)
                        raise CommandError("Download exceeded the 50 MB safety limit.")
                    handle.write(chunk)
    # WhatsApp file-upload APIs differ across Neonize versions. Use the adapter if available;
    # otherwise keep the downloaded file and report its path rather than pretending it was sent.
    sender = getattr(ctx.session, "send_file", None)
    if callable(sender):
        await sender(ctx.chat, str(target), caption=f"Downloaded by 𝚇𝙲𝙾𝚁𝙴®: {name}")
        status = "Downloaded and sent to this chat."
    else:
        status = "Downloaded to the bot host; this WhatsApp library adapter does not expose file sending."
    await ctx.reply(f"✅ MEDIAFIRE DOWNLOAD\nFile: {name}\nSize: {total / 1024 / 1024:.2f} MB\n{status}\nPath: {target}{footer(ctx.sender_number)}")


@command("save")
async def save_contact(ctx):
    args = list(ctx.args)
    if not args or args[0].lower() != "contact":
        raise CommandError("Usage: ®save contact <tag> <target-number> (or reply to a user)")
    args.pop(0)
    quoted = ctx.msg.quoted_participant
    if quoted:
        number = _number(quoted.split("@")[0].split(":")[0])
        tag = _clean_tag(" ".join(args) or "XCORE CONTACT")
    else:
        if len(args) < 2:
            raise CommandError("Usage: ®save contact <tag> <target-number>")
        number = _number(args[-1])
        tag = _clean_tag(" ".join(args[:-1]))
    if not 7 <= len(number) <= 15:
        raise CommandError("Target must be an international phone number (7–15 digits).")
    vcard = f"BEGIN:VCARD\nVERSION:3.0\nFN:{tag}\nTEL;TYPE=CELL:+{number}\nEND:VCARD"
    method = getattr(ctx.session, "send_contact", None)
    if callable(method):
        try:
            await method(ctx.chat, tag, number)
            result = "A WhatsApp contact card was sent."
        except Exception:
            await ctx.reply(f"CONTACT CARD\n{vcard}\n\nNative contact-card sending was unavailable; save these details to your address book manually.{footer(ctx.sender_number)}")
            return
    else:
        await ctx.reply(f"CONTACT CARD\n{vcard}\n\nSave these details to your address book manually.{footer(ctx.sender_number)}")
        return
    await ctx.reply(f"✅ {result}{footer(ctx.sender_number)}")


@command("readqr")
async def readqr(ctx):
    # QR decoding is limited to an image attached/replied to in the same chat.
    raw = ctx.msg.raw
    downloader = getattr(ctx.session, "download_media", None)
    if not callable(downloader):
        raise CommandError("This Neonize adapter does not expose image downloading yet. Reply to an image after enabling media download support.")
    try:
        image_data = await downloader(raw)
        if isinstance(image_data, str):
            image_data = Path(image_data).read_bytes()
        if not isinstance(image_data, (bytes, bytearray)):
            raise ValueError("Image bytes were not returned")
    except Exception as exc:
        raise CommandError(f"Could not download the replied image: {str(exc)[:160]}") from None
    try:
        import zxingcpp
        from PIL import Image
        import io
        decoded = zxingcpp.read_barcodes(Image.open(io.BytesIO(image_data)))
        values = [item.text for item in decoded if getattr(item, "text", "")]
    except ImportError:
        raise CommandError("QR decoder dependencies are missing. Install Pillow and zxing-cpp, then redeploy.") from None
    except Exception as exc:
        raise CommandError(f"Could not decode the image: {str(exc)[:160]}") from None
    if not values:
        raise CommandError("No readable QR code was found in the replied image.")
    # Display decoded text only. Never open URLs or run QR contents.
    output = "\n\n".join(f"{i}. {value[:1200]}" for i, value in enumerate(values[:5], 1))
    label = " ".join(ctx.args).strip()[:60]
    label_line = f"Tag: {label}\n" if label else ""
    await ctx.reply(f"🔎 QR SCAN RESULT\n{label_line}{output}\n\nDecoded content is shown as text only; XCORE did not open any links.{footer(ctx.sender_number)}")
