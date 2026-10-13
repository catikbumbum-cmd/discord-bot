"""
Discord Auto-Role Bot (hosting-ready for Render)
------------------------------------------------
Requires: Python 3.9+, discord.py 2.x, python-dotenv, flask
Install:  pip install -U discord.py python-dotenv flask

Setup:
  1. Locally: create ".env" next to this file:  DISCORD_TOKEN=your_token
     On Render: set DISCORD_TOKEN in Environment (do NOT upload .env!)
  2. Enable "Server Members Intent" + "Message Content Intent"
     in the Discord Developer Portal (Bot tab).
  3. Run:  python autorole.py
"""

import asyncio
import copy
import json
import os
from threading import Thread

import discord
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask

# ═══════════════════════════ TOKEN ═══════════════════════════

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

PREFIX = "!"
SETTINGS_FILE = "autorole_settings.json"

# ═══════════════════════════ CONFIG ═══════════════════════════

DEFAULTS = {
    "enabled": True,       # master switch
    "roles": [],           # role IDs given to new HUMAN members
    "bot_role": None,      # role ID given to new BOTS (optional)
    "delay": 0,            # wait N seconds before assigning (anti-raid)
    "dm_message": None,    # welcome DM text, supports {user} and {server}
    "log_channel": None,   # channel ID for join logs
}

intents = discord.Intents.default()
intents.message_content = True  # needed for commands
intents.members = True          # needed to detect new members

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

settings = {}
hierarchy_warned = set()

# ═══════════════════════ SETTINGS PERSISTENCE ═══════════════════════

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            settings.update({int(g): cfg for g, cfg in raw.items()})
        except (json.JSONDecodeError, OSError):
            pass

def save_settings():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass  # read-only disk on host = settings live in memory only

def cfg_for(guild_id: int) -> dict:
    if guild_id not in settings:
        settings[guild_id] = copy.deepcopy(DEFAULTS)
        save_settings()
    return settings[guild_id]

# ═══════════════════════ HELPERS ═══════════════════════

def assignable_roles(guild: discord.Guild, role_ids) -> list:
    """Only the roles the bot is actually allowed to give."""
    me = guild.me
    out = []
    for rid in role_ids:
        role = guild.get_role(rid)
        if (role and role != guild.default_role
                and not role.managed
                and me.top_role > role):
            out.append(role)
    return out

def setup_problem(guild: discord.Guild, role: discord.Role):
    """Human-readable problem string, or None if the role is fine."""
    me = guild.me
    if not me.guild_permissions.manage_roles:
        return ("I'm missing the **Manage Roles** permission. "
                "Server Settings → Roles → my role → enable **Manage Roles**.")
    if role == guild.default_role:
        return "you can't assign **@everyone**."
    if role.managed:
        return f"**{role.name}** is managed by another bot/integration."
    if role >= me.top_role:
        return (f"my role is **below** {role.mention}. Server Settings → Roles → "
                f"drag my role **above** it.")
    return None

def mod_only():
    async def predicate(ctx):
        if ctx.guild is None:
            return False
        p = ctx.author.guild_permissions
        return p.administrator or p.manage_roles
    return commands.check(predicate)

# ═══════════════════════ CORE LOGIC ═══════════════════════

async def warn_owner(guild: discord.Guild, text: str):
    cfg = cfg_for(guild.id)
    channel = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else guild.system_channel
    if channel:
        try:
            await channel.send(f"⚠️ **Auto-role problem:** {text}")
        except discord.HTTPException:
            pass

async def give_join_roles(member: discord.Member) -> list:
    guild = member.guild
    cfg = cfg_for(guild.id)
    wanted = [cfg["bot_role"]] if member.bot else [r for r in cfg["roles"] if r]

    if not wanted:
        return []

    roles = assignable_roles(guild, wanted)
    if not roles:
        if guild.id not in hierarchy_warned:
            hierarchy_warned.add(guild.id)
            names = ", ".join(f"<@&{r}>" for r in wanted)
            await warn_owner(guild,
                f"I can't assign {names}. Do I have **Manage Roles**, "
                f"and is my role **above** them?")
        return []

    # Optional delay (lets anti-raid tools ban raiders first)
    if cfg["delay"] > 0 and not member.bot:
        await asyncio.sleep(cfg["delay"])
        member = guild.get_member(member.id)
        if member is None:  # they left before the delay ended
            return []

    try:
        await member.add_roles(*roles, reason="Auto-role")
    except (discord.Forbidden, discord.HTTPException):
        return []

    return roles

async def send_join_log(guild, member, roles):
    cfg = cfg_for(guild.id)
    channel = guild.get_channel(cfg["log_channel"]) if cfg["log_channel"] else None
    if channel is None:
        return
    embed = discord.Embed(
        title="✅ Auto-role assigned",
        description=f"{member.mention} received: " + ", ".join(r.mention for r in roles),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass

# ═══════════════════════ EVENTS ═══════════════════════

@bot.event
async def on_member_join(member: discord.Member):
    cfg = cfg_for(member.guild.id)
    if not cfg["enabled"]:
        return

    # Optional welcome DM
    if cfg["dm_message"] and not member.bot:
        try:
            text = (cfg["dm_message"]
                    .replace("{user}", member.name)
                    .replace("{server}", member.guild.name))
            await member.send(text)
        except discord.HTTPException:
            pass  # DMs closed — no problem

    roles = await give_join_roles(member)
    if roles:
        await send_join_log(member.guild, member, roles)

@bot.event
async def on_ready():
    print(f"✅ Auto-role bot logged in as {bot.user} — serving {len(bot.guilds)} server(s)")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="for new members 🎭"))

# ═══════════════════════ COMMANDS ═══════════════════════

@bot.group(invoke_without_command=True)
@commands.guild_only()
async def autorole(ctx):
    await ctx.send(
        f"**Auto-Role** — subcommands:\n"
        f"`{PREFIX}autorole add <role>` — give new members this role\n"
        f"`{PREFIX}autorole remove <role>` — stop giving a role\n"
        f"`{PREFIX}autorole botrole <role|off>` — role for new bots\n"
        f"`{PREFIX}autorole delay <seconds>` — wait before assigning\n"
        f"`{PREFIX}autorole dm <text|off>` — welcome DM ({{user}}, {{server}})\n"
        f"`{PREFIX}autorole log <#channel>` — set log channel\n"
        f"`{PREFIX}autorole on` / `{PREFIX}autorole off`\n"
        f"`{PREFIX}autorole test <@user>` — test on an existing member\n"
        f"`{PREFIX}autorole show` — view settings"
    )

@autorole.command(name="add")
@mod_only()
async def autorole_add(ctx, *, role: discord.Role):
    cfg = cfg_for(ctx.guild.id)
    problem = setup_problem(ctx.guild, role)
    if problem:
        await ctx.reply(f"⚠️ Can't use that role: {problem}")
        return
    if role.id not in cfg["roles"]:
        cfg["roles"].append(role.id)
        save_settings()
    hierarchy_warned.discard(ctx.guild.id)
    await ctx.reply(f"✅ New members will now get {role.mention}.")

@autorole.command(name="remove")
@mod_only()
async def autorole_remove(ctx, *, role: discord.Role):
    cfg = cfg_for(ctx.guild.id)
    if role.id in cfg["roles"]:
        cfg["roles"].remove(role.id)
        save_settings()
        await ctx.reply(f"✅ {role.mention} removed from auto-roles.")
    else:
        await ctx.reply("That role isn't in the auto-role list.")

@autorole.command(name="botrole")
@mod_only()
async def autorole_botrole(ctx, *, role: discord.Role = None):
    cfg = cfg_for(ctx.guild.id)
    if role is None or role.name.lower() == "off":
        cfg["bot_role"] = None
        save_settings()
        await ctx.reply("✅ Bot auto-role disabled.")
        return
    problem = setup_problem(ctx.guild, role)
    if problem:
        await ctx.reply(f"⚠️ Can't use that role: {problem}")
        return
    cfg["bot_role"] = role.id
    save_settings()
    hierarchy_warned.discard(ctx.guild.id)
    await ctx.reply(f"✅ New bots will now get {role.mention}.")

@autorole.command(name="delay")
@mod_only()
async def autorole_delay(ctx, seconds: int = 0):
    if seconds < 0 or seconds > 600:
        await ctx.reply("Pick a delay between 0 and 600 seconds.")
        return
    cfg_for(ctx.guild.id)["delay"] = seconds
    save_settings()
    await ctx.reply(f"✅ Auto-role delay set to **{seconds}s**.")

@autorole.command(name="dm")
@mod_only()
async def autorole_dm(ctx, *, text: str):
    cfg = cfg_for(ctx.guild.id)
    if text.lower() == "off":
        cfg["dm_message"] = None
        save_settings()
        await ctx.reply("✅ Welcome DM disabled.")
    else:
        cfg["dm_message"] = text
        save_settings()
        await ctx.reply(f"✅ New members will get this DM:\n>>> {text}")

@autorole.command(name="log")
@mod_only()
async def autorole_log(ctx, channel: discord.TextChannel):
    cfg = cfg_for(ctx.guild.id)
    cfg["log_channel"] = channel.id
    save_settings()
    await ctx.reply(f"✅ Join logs will go to {channel.mention}.")

@autorole.command(name="on")
@mod_only()
async def autorole_on(ctx):
    cfg_for(ctx.guild.id)["enabled"] = True
    save_settings()
    await ctx.reply("✅ Auto-role enabled.")

@autorole.command(name="off")
@mod_only()
async def autorole_off(ctx):
    cfg_for(ctx.guild.id)["enabled"] = False
    save_settings()
    await ctx.reply("⏸️ Auto-role disabled.")

@autorole.command(name="test")
@mod_only()
async def autorole_test(ctx, member: discord.Member):
    roles = await give_join_roles(member)
    if roles:
        await ctx.reply(f"✅ Gave {member.mention}: " + ", ".join(r.mention for r in roles))
    else:
        cfg = cfg_for(ctx.guild.id)
        wanted = [cfg["bot_role"]] if member.bot else cfg["roles"]
        if not [r for r in wanted if r]:
            await ctx.reply(f"No auto-roles configured yet — use `{PREFIX}autorole add <role>`.")
        else:
            await ctx.reply("⚠️ Couldn't assign the role — check my **Manage Roles** "
                            "permission and that my role is **above** it.")

@autorole.command(name="show")
@mod_only()
async def autorole_show(ctx):
    cfg = cfg_for(ctx.guild.id)
    roles = ", ".join(f"<@&{r}>" for r in cfg["roles"]) or "(none)"
    bot_role = f"<@&{cfg['bot_role']}>" if cfg["bot_role"] else "(none)"
    log_ch = f"<#{cfg['log_channel']}>" if cfg["log_channel"] else "(none)"
    embed = discord.Embed(title=f"🎭 Auto-Role — {ctx.guild.name}", color=discord.Color.blurple())
    embed.add_field(name="Enabled", value="✅ Yes" if cfg["enabled"] else "❌ No", inline=True)
    embed.add_field(name="Delay", value=f"{cfg['delay']}s", inline=True)
    embed.add_field(name="Member roles", value=roles, inline=False)
    embed.add_field(name="Bot role", value=bot_role, inline=False)
    embed.add_field(name="Log channel", value=log_ch, inline=False)
    embed.add_field(name="Welcome DM", value=str(cfg["dm_message"] or "(off)")[:1024], inline=False)
    await ctx.reply(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
        try:
            await ctx.reply("❌ You need the **Manage Roles** permission for this.")
        except discord.HTTPException:
            pass
    elif isinstance(error, (commands.RoleNotFound, commands.MemberNotFound, commands.ChannelNotFound)):
        try:
            await ctx.reply("❌ Couldn't find that. Try mentioning the role/user/channel directly (@Name).")
        except discord.HTTPException:
            pass
    elif isinstance(error, commands.BadArgument):
        try:
            await ctx.reply("❌ Invalid argument — check usage with `!autorole`.")
        except discord.HTTPException:
            pass
    else:
        print(f"Command error: {error!r}")

# ═══════════════════════ KEEP ALIVE + RUN ═══════════════════════

web_app = Flask('')

@web_app.route('/')
def home():
    return "Auto-role bot is alive! ✅"

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    Thread(target=lambda: web_app.run(host='0.0.0.0', port=port), daemon=True).start()

if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN missing — set it in .env (local) or Render → Environment.")
    else:
        load_settings()
        keep_alive()   # must run BEFORE bot.run()
        bot.run(TOKEN)
