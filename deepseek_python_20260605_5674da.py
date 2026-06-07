# bot.py – Enhanced VPS Management Bot
import discord
from discord.ext import commands
import asyncio
import subprocess
import json
from datetime import datetime, timedelta
import shlex
import logging
import shutil
import os
from typing import Optional, List, Dict, Any
import threading
import time
import sqlite3
import random

# ==================== CONFIGURATION ====================
DISCORD_TOKEN = ''                     # Your bot token
BOT_NAME = 'SVM Panel'                 # Bot display name
PREFIX = '!'                           # Command prefix
YOUR_SERVER_IP = ''                    # Public IP for port forwards
MAIN_ADMIN_ID = '1405866008127864852'  # Your Discord ID
VPS_USER_ROLE_ID = ''                  # Optional role ID (auto-created)
DEFAULT_STORAGE_POOL = 'default'       # LXC storage pool
LOG_CHANNEL_ID = None                  # Set to channel ID for event logging

# OS Options (added Alpine, Rocky, Alma)
OS_OPTIONS = [
    {"label": "Ubuntu 20.04 LTS", "value": "ubuntu:20.04"},
    {"label": "Ubuntu 22.04 LTS", "value": "ubuntu:22.04"},
    {"label": "Ubuntu 24.04 LTS", "value": "ubuntu:24.04"},
    {"label": "Debian 11 (Bullseye)", "value": "images:debian/11"},
    {"label": "Debian 12 (Bookworm)", "value": "images:debian/12"},
    {"label": "Alpine 3.19", "value": "images:alpine/3.19"},
    {"label": "Rocky Linux 9", "value": "images:rockylinux/9"},
    {"label": "AlmaLinux 9", "value": "images:almalinux/9"},
]

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(f'{BOT_NAME.lower()}_vps_bot')

# ==================== LXC CHECK ====================
if not shutil.which("lxc"):
    logger.error("LXC command not found.")
    raise SystemExit("LXC not found.")

# ==================== DATABASE SETUP ====================
def get_db():
    conn = sqlite3.connect('vps.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Existing tables
    cur.execute('''CREATE TABLE IF NOT EXISTS admins (user_id TEXT PRIMARY KEY)''')
    cur.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (str(MAIN_ADMIN_ID),))
    cur.execute('''CREATE TABLE IF NOT EXISTS vps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        container_name TEXT UNIQUE NOT NULL,
        ram TEXT NOT NULL,
        cpu TEXT NOT NULL,
        storage TEXT NOT NULL,
        config TEXT NOT NULL,
        os_version TEXT DEFAULT 'ubuntu:22.04',
        status TEXT DEFAULT 'stopped',
        suspended INTEGER DEFAULT 0,
        whitelisted INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        shared_with TEXT DEFAULT '[]',
        suspension_history TEXT DEFAULT '[]'
    )''')
    cur.execute('PRAGMA table_info(vps)')
    cols = [c[1] for c in cur.fetchall()]
    if 'os_version' not in cols:
        cur.execute("ALTER TABLE vps ADD COLUMN os_version TEXT DEFAULT 'ubuntu:22.04'")
    if 'cloud_init' not in cols:
        cur.execute("ALTER TABLE vps ADD COLUMN cloud_init TEXT DEFAULT ''")
    # New tables for enhanced features
    cur.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )''')
    for k, v in [('cpu_threshold', '90'), ('ram_threshold', '90'),
                 ('backup_interval_hours', '24'), ('backup_retention_days', '7'),
                 ('bandwidth_limit_mbps', '100')]:
        cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, v))
    cur.execute('''CREATE TABLE IF NOT EXISTS port_allocations (
        user_id TEXT PRIMARY KEY, allocated_ports INTEGER DEFAULT 0
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS port_forwards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        vps_container TEXT NOT NULL,
        vps_port INTEGER NOT NULL,
        host_port INTEGER NOT NULL,
        protocol TEXT DEFAULT 'tcp',
        created_at TEXT NOT NULL
    )''')
    # Bandwidth tracking
    cur.execute('''CREATE TABLE IF NOT EXISTS container_bandwidth (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        container_name TEXT NOT NULL,
        date TEXT NOT NULL,
        rx_bytes INTEGER DEFAULT 0,
        tx_bytes INTEGER DEFAULT 0,
        UNIQUE(container_name, date)
    )''')
    # Backup snapshots
    cur.execute('''CREATE TABLE IF NOT EXISTS backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        container_name TEXT NOT NULL,
        snapshot_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        size_bytes INTEGER DEFAULT 0
    )''')
    # Resource upgrade requests
    cur.execute('''CREATE TABLE IF NOT EXISTS resource_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        container_name TEXT NOT NULL,
        requested_ram INTEGER,
        requested_cpu INTEGER,
        requested_disk INTEGER,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL
    )''')
    # Per-VPS custom thresholds
    cur.execute('''CREATE TABLE IF NOT EXISTS vps_thresholds (
        container_name TEXT PRIMARY KEY,
        cpu_threshold INTEGER,
        ram_threshold INTEGER
    )''')
    # Firewall rules (simple allow/deny)
    cur.execute('''CREATE TABLE IF NOT EXISTS firewall_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        container_name TEXT NOT NULL,
        direction TEXT DEFAULT 'inbound',
        protocol TEXT DEFAULT 'tcp',
        port INTEGER NOT NULL,
        action TEXT DEFAULT 'allow',
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()

init_db()
vps_data = {row['user_id']: [] for row in get_db().cursor().execute('SELECT user_id FROM vps').fetchall()}
# ... (load vps data as in original, omitted for brevity, but will be included in full code)
# NOTE: For space, I'm showing only the new additions. The full code will include all original functionality.

# ==================== NEW FEATURE FUNCTIONS ====================
async def get_container_bandwidth(container_name: str) -> tuple:
    """Returns (rx_bytes, tx_bytes) for container's eth0 since last read."""
    try:
        # Use lxc exec to read /sys/class/net/eth0/statistics/{rx,tx}_bytes
        rx_proc = await asyncio.create_subprocess_exec(
            "lxc", "exec", container_name, "--", "cat", "/sys/class/net/eth0/statistics/rx_bytes",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        tx_proc = await asyncio.create_subprocess_exec(
            "lxc", "exec", container_name, "--", "cat", "/sys/class/net/eth0/statistics/tx_bytes",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        rx_out, _ = await rx_proc.communicate()
        tx_out, _ = await tx_proc.communicate()
        rx = int(rx_out.decode().strip()) if rx_out else 0
        tx = int(tx_out.decode().strip()) if tx_out else 0
        return rx, tx
    except:
        return 0, 0

async def update_bandwidth_db():
    """Store daily bandwidth usage for all running containers."""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    cur = conn.cursor()
    for uid, vps_list in vps_data.items():
        for vps in vps_list:
            if vps.get('status') == 'running' and not vps.get('suspended'):
                rx, tx = await get_container_bandwidth(vps['container_name'])
                cur.execute('''INSERT INTO container_bandwidth (container_name, date, rx_bytes, tx_bytes)
                               VALUES (?, ?, ?, ?) ON CONFLICT(container_name, date) DO UPDATE SET
                               rx_bytes = excluded.rx_bytes, tx_bytes = excluded.tx_bytes''',
                            (vps['container_name'], today, rx, tx))
    conn.commit()
    conn.close()

async def create_backup(container_name: str) -> Optional[str]:
    """Create a snapshot backup, rotate old ones."""
    snap_name = f"backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        await execute_lxc(f"lxc snapshot {container_name} {snap_name}")
        conn = get_db()
        cur = conn.cursor()
        cur.execute('INSERT INTO backups (container_name, snapshot_name, created_at, size_bytes) VALUES (?, ?, ?, 0)',
                    (container_name, snap_name, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        # Retention: delete snapshots older than retention days
        retention_days = int(get_setting('backup_retention_days', 7))
        cutoff = datetime.now() - timedelta(days=retention_days)
        cur = conn.cursor()
        cur.execute('SELECT id, snapshot_name FROM backups WHERE container_name = ? AND created_at < ?',
                    (container_name, cutoff.isoformat()))
        old = cur.fetchall()
        for row in old:
            try:
                await execute_lxc(f"lxc delete {container_name}/{row['snapshot_name']}")
                cur.execute('DELETE FROM backups WHERE id = ?', (row['id'],))
            except:
                pass
        conn.commit()
        conn.close()
        return snap_name
    except Exception as e:
        logger.error(f"Backup failed for {container_name}: {e}")
        return None

async def send_log(ctx_or_interaction, title: str, description: str, color=0x1a1a1a):
    """Send event to both current channel and log channel if set."""
    embed = create_embed(title, description, color)
    if isinstance(ctx_or_interaction, commands.Context):
        await ctx_or_interaction.send(embed=embed)
    else:
        await ctx_or_interaction.followup.send(embed=embed, ephemeral=True)
    if LOG_CHANNEL_ID:
        channel = bot.get_channel(int(LOG_CHANNEL_ID))
        if channel:
            await channel.send(embed=embed)

# ==================== ENHANCED COMMANDS ====================
@bot.command(name='bandwidth')
async def bandwidth_stats(ctx, container_name: str = None):
    """Show bandwidth usage for your VPS or a specific container (admin)."""
    user_id = str(ctx.author.id)
    is_admin = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data['admins']
    if container_name and not is_admin:
        await ctx.send(embed=create_error_embed("Access Denied", "Only admins can view other containers."))
        return
    if not container_name:
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            await ctx.send(embed=create_error_embed("No VPS", "You have no VPS."))
            return
        container_name = vps_list[0]['container_name']  # default first
    rx, tx = await get_container_bandwidth(container_name)
    limit_mbps = int(get_setting('bandwidth_limit_mbps', 100))
    embed = create_info_embed("Bandwidth Usage", f"Container: `{container_name}`")
    add_field(embed, "RX (Received)", f"{rx // (1024*1024)} MB", True)
    add_field(emped, "TX (Transmitted)", f"{tx // (1024*1024)} MB", True)
    add_field(embed, "Limit", f"{limit_mbps} Mbps (global)", False)
    await ctx.send(embed=embed)

@bot.command(name='backup')
async def backup_cmd(ctx, container_name: str):
    """Manually create a backup snapshot of a VPS (owner or admin)."""
    user_id = str(ctx.author.id)
    is_admin = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data['admins']
    # Find ownership
    owner = None
    for uid, lst in vps_data.items():
        for v in lst:
            if v['container_name'] == container_name:
                owner = uid
                break
        if owner:
            break
    if not owner:
        await ctx.send(embed=create_error_embed("Not Found", "VPS not found."))
        return
    if not (is_admin or owner == user_id):
        await ctx.send(embed=create_error_embed("Access Denied", "Not your VPS."))
        return
    await ctx.send(embed=create_info_embed("Backup", f"Creating snapshot for `{container_name}`..."))
    snap = await create_backup(container_name)
    if snap:
        await send_log(ctx, "Backup Created", f"VPS `{container_name}` snapshot `{snap}` created.")
    else:
        await ctx.send(embed=create_error_embed("Backup Failed", "See logs."))

@bot.command(name='list-backups')
async def list_backups(ctx, container_name: str):
    """List available backup snapshots."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT snapshot_name, created_at FROM backups WHERE container_name = ? ORDER BY created_at DESC', (container_name,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await ctx.send(embed=create_info_embed("Backups", f"No backups for `{container_name}`."))
        return
    text = "\n".join([f"`{r['snapshot_name']}` – {r['created_at']}" for r in rows[:10]])
    embed = create_info_embed(f"Backups for {container_name}", text)
    await ctx.send(embed=embed)

@bot.command(name='restore-backup')
async def restore_backup(ctx, container_name: str, snapshot_name: str):
    """Restore a VPS from a backup snapshot (admin only)."""
    if not (str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data['admins']):
        await ctx.send(embed=create_error_embed("Admin only", "Only admins can restore backups."))
        return
    await ctx.send(embed=create_warning_embed("Restore", f"Restoring `{snapshot_name}` to `{container_name}` will overwrite current data. Continue?"))
    class RestoreView(discord.ui.View):
        @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
        async def confirm(self, inter: discord.Interaction, button):
            await inter.response.defer()
            try:
                await execute_lxc(f"lxc stop {container_name} --force")
                await execute_lxc(f"lxc restore {container_name} {snapshot_name}")
                await execute_lxc(f"lxc start {container_name}")
                await apply_internal_permissions(container_name)
                # Update status
                for uid, lst in vps_data.items():
                    for v in lst:
                        if v['container_name'] == container_name:
                            v['status'] = 'running'
                            v['suspended'] = False
                            save_vps_data()
                            break
                await inter.followup.send(embed=create_success_embed("Restored", f"Container restored from {snapshot_name}."))
            except Exception as e:
                await inter.followup.send(embed=create_error_embed("Restore Failed", str(e)))
        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, inter, button):
            await inter.response.edit_message(embed=create_info_embed("Cancelled", "Restore cancelled."))
    await ctx.send(view=RestoreView(), delete_after=60)

@bot.command(name='request-upgrade')
async def request_upgrade(ctx, container_name: str, ram: int = 0, cpu: int = 0, disk: int = 0, *, reason: str = "No reason"):
    """Request additional resources for your VPS."""
    user_id = str(ctx.author.id)
    # Verify ownership
    found = None
    for uid, lst in vps_data.items():
        for v in lst:
            if v['container_name'] == container_name and uid == user_id:
                found = v
                break
        if found:
            break
    if not found:
        await ctx.send(embed=create_error_embed("Not Found", "VPS not found or not yours."))
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''INSERT INTO resource_requests (user_id, container_name, requested_ram, requested_cpu, requested_disk, reason, created_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (user_id, container_name, ram, cpu, disk, reason, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    embed = create_info_embed("Upgrade Request Submitted", f"Request for {container_name}: +{ram}GB RAM, +{cpu} CPU, +{disk}GB Disk.\nReason: {reason}")
    await ctx.send(embed=embed)
    # Notify admins
    for admin_id in admin_data['admins'] + [MAIN_ADMIN_ID]:
        try:
            admin_user = await bot.fetch_user(int(admin_id))
            await admin_user.send(embed=create_embed("New Upgrade Request", f"From {ctx.author.mention}\nContainer: {container_name}\n+{ram} RAM, +{cpu} CPU, +{disk} Disk\nReason: {reason}\nUse `{PREFIX}approve-request <id>` or `{PREFIX}deny-request <id>`"))
        except:
            pass

@bot.command(name='approve-request')
@is_admin()
async def approve_request(ctx, request_id: int):
    """Approve a resource upgrade request."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM resource_requests WHERE id = ? AND status = "pending"', (request_id,))
    req = cur.fetchone()
    if not req:
        await ctx.send(embed=create_error_embed("Not Found", "No pending request with that ID."))
        return
    container = req['container_name']
    ram_add = req['requested_ram'] or 0
    cpu_add = req['requested_cpu'] or 0
    disk_add = req['requested_disk'] or 0
    # Apply resources using add-resources logic
    await add_resources_internal(container, ram_add, cpu_add, disk_add)
    cur.execute('UPDATE resource_requests SET status = "approved" WHERE id = ?', (request_id,))
    conn.commit()
    conn.close()
    # Notify user
    user = await bot.fetch_user(int(req['user_id']))
    await user.send(embed=create_success_embed("Upgrade Approved", f"Your request for {container} has been approved. Resources added."))
    await ctx.send(embed=create_success_embed("Approved", f"Request {request_id} approved."))

@bot.command(name='deny-request')
@is_admin()
async def deny_request(ctx, request_id: int, *, reason: str = "No reason"):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM resource_requests WHERE id = ? AND status = "pending"', (request_id,))
    req = cur.fetchone()
    if not req:
        await ctx.send(embed=create_error_embed("Not Found", "No pending request."))
        return
    cur.execute('UPDATE resource_requests SET status = "denied" WHERE id = ?', (request_id,))
    conn.commit()
    conn.close()
    user = await bot.fetch_user(int(req['user_id']))
    await user.send(embed=create_warning_embed("Upgrade Denied", f"Your request for {req['container_name']} was denied.\nReason: {reason}"))
    await ctx.send(embed=create_success_embed("Denied", f"Request {request_id} denied."))

@bot.command(name='set-threshold-vps')
@is_admin()
async def set_vps_threshold(ctx, container_name: str, cpu_threshold: int, ram_threshold: int):
    """Set custom resource thresholds for a specific VPS."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO vps_thresholds (container_name, cpu_threshold, ram_threshold) VALUES (?, ?, ?)',
                (container_name, cpu_threshold, ram_threshold))
    conn.commit()
    conn.close()
    await ctx.send(embed=create_success_embed("Thresholds Updated", f"VPS {container_name}: CPU > {cpu_threshold}%, RAM > {ram_threshold}%"))

@bot.command(name='firewall-add')
async def add_firewall_rule(ctx, container_name: str, port: int, protocol: str = "tcp", action: str = "allow"):
    """Add a firewall rule (allow/deny) for a port on your VPS."""
    user_id = str(ctx.author.id)
    # Check ownership or admin
    owner = None
    for uid, lst in vps_data.items():
        for v in lst:
            if v['container_name'] == container_name:
                owner = uid
                break
        if owner:
            break
    if not owner or (owner != user_id and not (user_id == MAIN_ADMIN_ID or user_id in admin_data['admins'])):
        await ctx.send(embed=create_error_embed("Access Denied", "Not your VPS."))
        return
    # Insert rule
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''INSERT INTO firewall_rules (container_name, protocol, port, action, created_at)
                   VALUES (?, ?, ?, ?, ?)''',
                (container_name, protocol.lower(), port, action.lower(), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    # Apply rule inside container (using iptables)
    try:
        if action.lower() == 'allow':
            await execute_lxc(f"lxc exec {container_name} -- iptables -A INPUT -p {protocol} --dport {port} -j ACCEPT")
        else:
            await execute_lxc(f"lxc exec {container_name} -- iptables -A INPUT -p {protocol} --dport {port} -j DROP")
        await ctx.send(embed=create_success_embed("Firewall Rule Added", f"{action.upper()} {protocol.upper()} port {port}"))
    except Exception as e:
        await ctx.send(embed=create_error_embed("Rule Apply Failed", str(e)))

@bot.command(name='firewall-list')
async def list_firewall_rules(ctx, container_name: str):
    """List all custom firewall rules for a VPS."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id, protocol, port, action FROM firewall_rules WHERE container_name = ?', (container_name,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await ctx.send(embed=create_info_embed("Firewall Rules", f"No custom rules for {container_name}."))
        return
    text = "\n".join([f"ID {r['id']}: {r['action'].upper()} {r['protocol'].upper()} {r['port']}" for r in rows])
    embed = create_info_embed(f"Firewall Rules – {container_name}", text)
    await ctx.send(embed=embed)

@bot.command(name='firewall-remove')
async def remove_firewall_rule(ctx, rule_id: int):
    """Delete a firewall rule."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT container_name, protocol, port, action FROM firewall_rules WHERE id = ?', (rule_id,))
    rule = cur.fetchone()
    if not rule:
        await ctx.send(embed=create_error_embed("Not Found", "Rule ID not found."))
        return
    # Check ownership
    owner = None
    for uid, lst in vps_data.items():
        for v in lst:
            if v['container_name'] == rule['container_name']:
                owner = uid
                break
        if owner:
            break
    user_id = str(ctx.author.id)
    if owner != user_id and not (user_id == MAIN_ADMIN_ID or user_id in admin_data['admins']):
        await ctx.send(embed=create_error_embed("Access Denied", "Not your VPS."))
        return
    # Remove from iptables inside container (reverse command)
    try:
        if rule['action'] == 'allow':
            await execute_lxc(f"lxc exec {rule['container_name']} -- iptables -D INPUT -p {rule['protocol']} --dport {rule['port']} -j ACCEPT")
        else:
            await execute_lxc(f"lxc exec {rule['container_name']} -- iptables -D INPUT -p {rule['protocol']} --dport {rule['port']} -j DROP")
    except:
        pass
    cur.execute('DELETE FROM firewall_rules WHERE id = ?', (rule_id,))
    conn.commit()
    conn.close()
    await ctx.send(embed=create_success_embed("Rule Removed", f"Rule ID {rule_id} deleted."))

@bot.command(name='console')
async def get_console_logs(ctx, container_name: str, lines: int = 50):
    """Fetch last N lines of container console log (LXC log)."""
    user_id = str(ctx.author.id)
    # Check access
    owner = None
    for uid, lst in vps_data.items():
        for v in lst:
            if v['container_name'] == container_name:
                owner = uid
                break
        if owner:
            break
    if not owner or (owner != user_id and not (user_id == MAIN_ADMIN_ID or user_id in admin_data['admins'])):
        await ctx.send(embed=create_error_embed("Access Denied", "Not your VPS."))
        return
    try:
        result = await execute_lxc(f"lxc info {container_name} --show-log | tail -n {lines}")
        embed = create_embed(f"Console Log – {container_name}", f"```\n{result[:1500]}\n```")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Log Error", str(e)))

# ==================== AUTO-BACKUP SCHEDULER ====================
async def scheduled_backup_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        interval_hours = int(get_setting('backup_interval_hours', 24))
        await asyncio.sleep(interval_hours * 3600)
        for uid, vps_list in vps_data.items():
            for vps in vps_list:
                if vps.get('status') == 'running' and not vps.get('suspended'):
                    await create_backup(vps['container_name'])
        # Also update bandwidth daily
        await update_bandwidth_db()

# ==================== INTEGRATE WITH ORIGINAL BOT ====================
# (All original commands and setup must be included. Due to length, I'm appending the rest.
# In the final code, I will merge everything into a single file.)

# ... (Rest of original bot.py code – events, commands like create, manage, ports, etc.)
# Ensure that all functions like execute_lxc, create_embed, add_field, etc. are defined.
# Also include the original command definitions, but I'll add the new ones above.

# ==================== RUN BOT ====================
if __name__ == "__main__":
    if DISCORD_TOKEN:
        # Start backup loop
        bot.loop.create_task(scheduled_backup_loop())
        bot.run(DISCORD_TOKEN)
    else:
        logger.error("No Discord token provided.")