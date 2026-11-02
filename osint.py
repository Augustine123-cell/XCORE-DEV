"""OSINT commands. Premium-gated by the handler; provider-backed ones fail closed until configured."""
from ...osint import tgage
from . import CommandError, command
from .general import pretty_json


@command("agecheck")
async def agecheck(ctx):
    if not ctx.args:
        raise CommandError("Usage: ®agecheck <id>")
    await ctx.reply(pretty_json(await tgage.age(ctx.args[0])))


@command("tgage")
async def tgage_cmd(ctx):
    if not ctx.args:
        raise CommandError("Usage: ®tgage <id>")
    await ctx.reply(pretty_json(await tgage.age(ctx.args[0])))


def _static(name: str, text: str):
    async def run(ctx):
        await ctx.reply(text)

    run.__name__ = name
    command(name)(run)


_static("wafind", "🔎 WA FIND is disabled until an authorized provider is configured.")
_static("getwadetail", "ℹ️ WhatsApp detail lookup requires a configured authorized provider.")
_static("getgcdetail", "ℹ️ Group detail uses only data available to the connected session.")
_static("tgfind", "🔎 Telegram search provider is not configured.")
_static("tgdetail", "ℹ️ Telegram profile lookup requires an authorized provider.")
_static("tggcdetail", "ℹ️ Telegram group/channel lookup requires an authorized provider.")
