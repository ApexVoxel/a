import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiosqlite
from datetime import datetime, timedelta
import re
import random

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=".", intents=intents)

DB_PATH = "bot_data.db"

# -------------------- Database Setup --------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Giveaways table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                channel_id INTEGER,
                message_id INTEGER UNIQUE,
                prize TEXT,
                winners_count INTEGER,
                end_time TIMESTAMP,
                ended BOOLEAN DEFAULT 0
            )
        """)
        # Participants table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_participants (
                giveaway_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (giveaway_id, user_id)
            )
        """)
        # Ticket system config (one per guild)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message_id INTEGER,
                title TEXT,
                description TEXT,
                button_label TEXT
            )
        """)
        await db.commit()

# -------------------- Helpers --------------------
def parse_duration(duration_str: str) -> int:
    match = re.match(r"(\d+)(h)", duration_str.lower())
    if not match:
        raise ValueError("Invalid duration. Use format like '1h' or '5h'.")
    hours = int(match.group(1))
    return hours * 3600

async def get_category_uncatgre(guild: discord.Guild):
    category = discord.utils.get(guild.categories, name="uncatgre")
    if not category:
        category = await guild.create_category("uncatgre")
    return category

# -------------------- Giveaway Views / Tasks --------------------
active_giveaway_tasks = {}

async def end_giveaway(giveaway_id: int, message_id: int, channel_id: int, guild_id: int, prize: str, winners_count: int):
    await asyncio.sleep(0)  # placeholder for actual timer
    # Actually called from task after delay
    async with aiosqlite.connect(DB_PATH) as db:
        # Mark as ended
        await db.execute("UPDATE giveaways SET ended = 1 WHERE id = ?", (giveaway_id,))
        # Fetch participants
        cursor = await db.execute("SELECT user_id FROM giveaway_participants WHERE giveaway_id = ?", (giveaway_id,))
        participants = [row[0] async for row in cursor]
        await db.commit()

    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(message_id)
    except:
        return

    if not participants:
        await channel.send(f"❌ Giveaway **{prize}** ended with **0** entrants. No winners!")
        await msg.edit(content="❌ No participants, giveaway cancelled.", embed=None)
        return

    winners = random.sample(participants, min(winners_count, len(participants)))
    winner_mentions = [f"<@{uid}>" for uid in winners]
    await channel.send(f"🎉 **Giveaway Ended!** 🎉\nPrize: **{prize}**\nWinners: {', '.join(winner_mentions)}")

    # Update embed
    embed = discord.Embed(title="Giveaway Ended", description=f"**Prize:** {prize}\n**Winners:** {', '.join(winner_mentions)}", color=discord.Color.red())
    await msg.edit(embed=embed, content=None)

async def schedule_giveaway_end(giveaway_id: int, message_id: int, channel_id: int, guild_id: int, prize: str, winners_count: int, end_dt: datetime):
    now = datetime.utcnow()
    delay = (end_dt - now).total_seconds()
    if delay <= 0:
        await end_giveaway(giveaway_id, message_id, channel_id, guild_id, prize, winners_count)
        return

    async def task():
        await asyncio.sleep(delay)
        await end_giveaway(giveaway_id, message_id, channel_id, guild_id, prize, winners_count)
        if giveaway_id in active_giveaway_tasks:
            del active_giveaway_tasks[giveaway_id]

    t = asyncio.create_task(task())
    active_giveaway_tasks[giveaway_id] = t

@bot.event
async def on_ready():
    await init_db()
    print(f"Logged in as {bot.user}")
    # Reload active giveaways on restart
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, message_id, channel_id, guild_id, prize, winners_count, end_time FROM giveaways WHERE ended = 0 AND end_time > ?", (datetime.utcnow(),)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                giveaway_id, msg_id, ch_id, g_id, prize, w_count, end_time_str = row
                end_time = datetime.fromisoformat(end_time_str)
                await schedule_giveaway_end(giveaway_id, msg_id, ch_id, g_id, prize, w_count, end_time)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != "🎉":
        return
    # Check if this reaction is on a giveaway message
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, ended FROM giveaways WHERE message_id = ? AND guild_id = ?", (payload.message_id, payload.guild_id))
        row = await cursor.fetchone()
        if not row:
            return
        giveaway_id, ended = row
        if ended:
            return
        # Add participant (ignore duplicates, PK handles)
        try:
            await db.execute("INSERT INTO giveaway_participants (giveaway_id, user_id) VALUES (?, ?)", (giveaway_id, payload.user_id))
            await db.commit()
        except:
            pass

# -------------------- Giveaway Commands --------------------
@bot.command(name="giveaway")
@commands.has_permissions(manage_guild=True)
async def giveaway_cmd(ctx, action: str, *args):
    if action.lower() == "create":
        if len(args) < 3:
            await ctx.send("Usage: `.giveaway create <wins:1-6> <prize> <duration:1h/5h>`")
            return
        try:
            wins = int(args[0])
            if wins < 1 or wins > 6:
                raise ValueError
            prize = args[1]
            duration_str = args[2]
            seconds = parse_duration(duration_str)
            end_time = datetime.utcnow() + timedelta(seconds=seconds)
        except ValueError as e:
            await ctx.send(f"Invalid parameters: {e}")
            return

        embed = discord.Embed(title="🎉 Giveaway 🎉", description=f"**Prize:** {prize}\n**Winners:** {wins}\n**Ends:** <t:{int(end_time.timestamp())}:R>", color=discord.Color.green())
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🎉")

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("INSERT INTO giveaways (guild_id, channel_id, message_id, prize, winners_count, end_time) VALUES (?, ?, ?, ?, ?, ?)",
                                      (ctx.guild.id, ctx.channel.id, msg.id, prize, wins, end_time.isoformat()))
            await db.commit()
            giveaway_id = cursor.lastrowid

        await schedule_giveaway_end(giveaway_id, msg.id, ctx.channel.id, ctx.guild.id, prize, wins, end_time)
        await ctx.send(f"✅ Giveaway created! Ends {duration_str}.", delete_after=5)

    elif action.lower() == "end":
        if len(args) < 1:
            await ctx.send("Usage: `.giveaway end <message_id>`")
            return
        try:
            msg_id = int(args[0])
        except:
            await ctx.send("Invalid message ID.")
            return

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, message_id, channel_id, guild_id, prize, winners_count, ended FROM giveaways WHERE message_id = ? AND guild_id = ?", (msg_id, ctx.guild.id))
            row = await cursor.fetchone()
            if not row:
                await ctx.send("Giveaway not found for this message.")
                return
            giveaway_id, db_msg_id, ch_id, g_id, prize, w_count, ended = row
            if ended:
                await ctx.send("Giveaway already ended.")
                return
            # Cancel scheduled task if exists
            if giveaway_id in active_giveaway_tasks:
                active_giveaway_tasks[giveaway_id].cancel()
                del active_giveaway_tasks[giveaway_id]
            # Force end
            await end_giveaway(giveaway_id, db_msg_id, ch_id, g_id, prize, w_count)

    else:
        await ctx.send("Unknown action. Use `create` or `end`.")

# -------------------- Ticket System --------------------
class TicketButton(discord.ui.View):
    def __init__(self, guild_id: int, label: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.button_label = label

    @discord.ui.button(label="Loading...", style=discord.ButtonStyle.primary, custom_id="ticket_button_placeholder")
    async def ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Actually the button's label and custom_id will be overridden in the send method.
        # We'll build the view dynamically in the command.
        pass

async def send_ticket_panel(channel: discord.TextChannel, title: str, description: str, button_label: str, guild_id: int):
    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    view = discord.ui.View(timeout=None)
    # Use a custom_id that includes guild_id so we can retrieve config
    custom_id = f"ticket_open_{guild_id}"
    view.add_item(discord.ui.Button(label=button_label, style=discord.ButtonStyle.primary, custom_id=custom_id))
    msg = await channel.send(embed=embed, view=view)
    return msg

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data:
        return
    custom_id = interaction.data.get("custom_id")
    if not custom_id or not custom_id.startswith("ticket_open_"):
        return
    guild_id = int(custom_id.split("_")[-1])
    if interaction.guild.id != guild_id:
        await interaction.response.send_message("This ticket panel is not for this server.", ephemeral=True)
        return

    # Fetch config for this guild
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT title, description FROM ticket_config WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        if not row:
            await interaction.response.send_message("Ticket system not configured.", ephemeral=True)
            return

    # Create ticket channel under "uncatgre"
    category = await get_category_uncatgre(interaction.guild)
    user = interaction.user
    ticket_name = f"ticket-{user.name}-{user.discriminator}" if user.discriminator != "0" else f"ticket-{user.name}"
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
        interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
    }
    try:
        ticket_channel = await interaction.guild.create_text_channel(ticket_name, category=category, overwrites=overwrites)
    except Exception as e:
        await interaction.response.send_message(f"Failed to create ticket channel: {e}", ephemeral=True)
        return

    # Send welcome message with close button
    close_view = discord.ui.View()
    close_view.add_item(discord.ui.Button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id=f"ticket_close_{ticket_channel.id}"))
    await ticket_channel.send(f"{user.mention}, welcome! Describe your issue.\nSupport will assist you shortly.", view=close_view)

    await interaction.response.send_message(f"Ticket created: {ticket_channel.mention}", ephemeral=True)

    # Optional log to a mod channel
    log_channel = interaction.guild.system_channel
    if log_channel:
        await log_channel.send(f"📬 Ticket opened by {user.mention} in {ticket_channel.mention}")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Handle close button interactions
    if not interaction.data:
        return
    custom_id = interaction.data.get("custom_id")
    if custom_id and custom_id.startswith("ticket_close_"):
        channel_id = int(custom_id.split("_")[-1])
        if interaction.channel.id != channel_id:
            await interaction.response.send_message("You can only close a ticket from inside that ticket channel.", ephemeral=True)
            return
        await interaction.response.send_message("Deleting this ticket channel in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()
        return
    # For ticket open button we already have a handler above; but due to multiple on_interaction,
    # the previous one must be merged. We'll refactor both handlers into one.
    # Actually the previous on_interaction will be overwritten if we define another. We'll combine both.
    # Let's restructure: one on_interaction that checks both open and close.

# Merged on_interaction (replace the two separate ones)
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data:
        return
    custom_id = interaction.data.get("custom_id")
    if not custom_id:
        return

    # Ticket open button
    if custom_id.startswith("ticket_open_"):
        guild_id = int(custom_id.split("_")[-1])
        if interaction.guild.id != guild_id:
            await interaction.response.send_message("This ticket panel is not for this server.", ephemeral=True)
            return
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT title, description FROM ticket_config WHERE guild_id = ?", (guild_id,))
            row = await cursor.fetchone()
            if not row:
                await interaction.response.send_message("Ticket system not configured.", ephemeral=True)
                return
        # Create ticket channel under "uncatgre"
        category = await get_category_uncatgre(interaction.guild)
        user = interaction.user
        ticket_name = f"ticket-{user.name}-{user.discriminator}" if user.discriminator != "0" else f"ticket-{user.name}"
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        try:
            ticket_channel = await interaction.guild.create_text_channel(ticket_name, category=category, overwrites=overwrites)
        except Exception as e:
            await interaction.response.send_message(f"Failed to create ticket channel: {e}", ephemeral=True)
            return
        close_view = discord.ui.View()
        close_view.add_item(discord.ui.Button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id=f"ticket_close_{ticket_channel.id}"))
        await ticket_channel.send(f"{user.mention}, welcome! Describe your issue.\nSupport will assist you shortly.", view=close_view)
        await interaction.response.send_message(f"Ticket created: {ticket_channel.mention}", ephemeral=True)
        log_channel = interaction.guild.system_channel
        if log_channel:
            await log_channel.send(f"📬 Ticket opened by {user.mention} in {ticket_channel.mention}")
        return

    # Ticket close button
    if custom_id.startswith("ticket_close_"):
        channel_id = int(custom_id.split("_")[-1])
        if interaction.channel.id != channel_id:
            await interaction.response.send_message("You can only close a ticket from inside that ticket channel.", ephemeral=True)
            return
        await interaction.response.send_message("Deleting this ticket channel in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()
        return

# -------------------- Ticketsys Commands --------------------
@bot.group(name="ticketsys")
@commands.has_permissions(manage_guild=True)
async def ticketsys_cmd(ctx):
    if ctx.invoked_subcommand is None:
        # Show current config if exists
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT channel_id, message_id, title, description, button_label FROM ticket_config WHERE guild_id = ?", (ctx.guild.id,))
            row = await cursor.fetchone()
            if row:
                ch_id, msg_id, title, desc, btn = row
                await ctx.send(f"**Current Ticket Config**\nChannel: <#{ch_id}>\nTitle: {title}\nDescription: {desc}\nButton: {btn}\n(Message ID: {msg_id})")
            else:
                await ctx.send("No ticket system configured. Use `.ticketsys #channel add <title> <description> <button_name>`")

@ticketsys_cmd.command(name="add")
async def ticketsys_add(ctx, channel: discord.TextChannel, title: str, description: str, button_name: str):
    """Add ticket panel to a channel"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Remove old config + message if exists
        cursor = await db.execute("SELECT message_id FROM ticket_config WHERE guild_id = ?", (ctx.guild.id,))
        old = await cursor.fetchone()
        if old:
            old_msg_id = old[0]
            try:
                # attempt to delete old panel message
                old_ch = ctx.guild.get_channel(channel.id)  # we don't know old channel, but we stored channel_id? Actually we didn't.
                # We'll store channel_id as well. Let's re-fetch full old config
                cursor2 = await db.execute("SELECT channel_id, message_id FROM ticket_config WHERE guild_id = ?", (ctx.guild.id,))
                old_data = await cursor2.fetchone()
                if old_data:
                    old_ch_id, old_msg_id = old_data
                    old_channel = ctx.guild.get_channel(old_ch_id)
                    if old_channel:
                        try:
                            old_msg = await old_channel.fetch_message(old_msg_id)
                            await old_msg.delete()
                        except:
                            pass
            except:
                pass
            await db.execute("DELETE FROM ticket_config WHERE guild_id = ?", (ctx.guild.id,))
        # Send new panel
        msg = await send_ticket_panel(channel, title, description, button_name, ctx.guild.id)
        await db.execute("INSERT INTO ticket_config (guild_id, channel_id, message_id, title, description, button_label) VALUES (?, ?, ?, ?, ?, ?)",
                         (ctx.guild.id, channel.id, msg.id, title, description, button_name))
        await db.commit()
    await ctx.send(f"✅ Ticket panel sent to {channel.mention}")

@ticketsys_cmd.command(name="remove")
async def ticketsys_remove(ctx):
    """Remove ticket configuration and delete the panel message"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT channel_id, message_id FROM ticket_config WHERE guild_id = ?", (ctx.guild.id,))
        row = await cursor.fetchone()
        if not row:
            await ctx.send("No ticket configuration found for this server.").    
            return
        ch_id, msg_id = row
        channel = ctx.guild.get_channel(ch_id)
        if channel:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.delete()
            except:
                pass
        await db.execute("DELETE FROM ticket_config WHERE guild_id = ?", (ctx.guild.id,))
        await db.commit()
    await ctx.send("✅ Ticket configuration removed and panel deleted.")

# -------------------- Run Bot --------------------
if __name__ == "__main__":
    bot.run("YOUR_BOT_TOKEN")
