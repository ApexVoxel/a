# bot.py — ApexVoxel Deploy Service ⚡ Ultra-Premium Edition
import discord
from discord.ext import commands
import asyncio
import subprocess
import json
from datetime import datetime
import shlex
import logging
import shutil
import os
from typing import Optional, List, Dict, Any
import threading
import time
import sqlite3
import random

# ──────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────
DISCORD_TOKEN      = os.getenv('DISCORD_TOKEN', 'YOUR BOT TOKEN')
BOT_NAME           = os.getenv('BOT_NAME', 'ApexVoxel')
PREFIX             = os.getenv('PREFIX', '!')
YOUR_SERVER_IP     = os.getenv('YOUR_SERVER_IP', '127.0.0.1')
MAIN_ADMIN_ID      = int(os.getenv('MAIN_ADMIN_ID', '1455801921150386298'))
VPS_USER_ROLE_ID   = int(os.getenv('VPS_USER_ROLE_ID', '1210291131301101618'))
DEFAULT_STORAGE_POOL = os.getenv('DEFAULT_STORAGE_POOL', 'default')

# ──────────────────────────────────────────────
#  OS OPTIONS
# ──────────────────────────────────────────────
OS_OPTIONS = [
    {"label": "Ubuntu 20.04 LTS",    "value": "ubuntu:20.04",      "emoji": "🟠"},
    {"label": "Ubuntu 22.04 LTS",    "value": "ubuntu:22.04",      "emoji": "🟠"},
    {"label": "Ubuntu 24.04 LTS",    "value": "ubuntu:24.04",      "emoji": "🟠"},
    {"label": "Debian 10 (Buster)",  "value": "images:debian/10",  "emoji": "🔴"},
    {"label": "Debian 11 (Bullseye)","value": "images:debian/11",  "emoji": "🔴"},
    {"label": "Debian 12 (Bookworm)","value": "images:debian/12",  "emoji": "🔴"},
    {"label": "Debian 13 (Trixie)",  "value": "images:debian/13",  "emoji": "🔴"},
]

# ──────────────────────────────────────────────
#  PREMIUM COLOUR PALETTE  (Cyber-Neon Theme)
# ──────────────────────────────────────────────
class Colors:
    PRIMARY    = 0x6C63FF   # Neon Violet – brand
    SUCCESS    = 0x00E5A0   # Cyber Mint
    ERROR      = 0xFF3366   # Neon Crimson
    WARNING    = 0xFFB300   # Solar Amber
    INFO       = 0x00B4D8   # Ice Blue
    GOLD       = 0xFFD700   # Pure Gold
    DARK       = 0x0D1117   # Obsidian
    MUTED      = 0x2B2D31   # Graphite
    RUNNING    = 0x00E5A0   # Cyber Mint
    STOPPED    = 0xFFB300   # Solar Amber
    SUSPENDED  = 0xFF3366   # Neon Crimson
    PURPLE     = 0xAB47BC   # Orchid
    TEAL       = 0x00BFA5   # Teal
    PINK       = 0xF50057   # Hot Pink
    NAVY       = 0x1A237E   # Deep Navy
    LIME       = 0x76FF03   # Volt Green

# ──────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(f'{BOT_NAME.lower()}_bot')

# ──────────────────────────────────────────────
#  LXC CHECK
# ──────────────────────────────────────────────
if not shutil.which("lxc"):
    logger.error("LXC command not found. Please ensure LXC is installed.")
    raise SystemExit("LXC command not found.")

# ──────────────────────────────────────────────
#  DATABASE
# ──────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect('vps.db')
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS admins (user_id TEXT PRIMARY KEY)''')
    cur.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (str(MAIN_ADMIN_ID),))
    cur.execute('''CREATE TABLE IF NOT EXISTS vps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        container_name TEXT UNIQUE NOT NULL,
        ram TEXT NOT NULL, cpu TEXT NOT NULL, storage TEXT NOT NULL,
        config TEXT NOT NULL, os_version TEXT DEFAULT 'ubuntu:22.04',
        status TEXT DEFAULT 'stopped', suspended INTEGER DEFAULT 0,
        whitelisted INTEGER DEFAULT 0, created_at TEXT NOT NULL,
        shared_with TEXT DEFAULT '[]', suspension_history TEXT DEFAULT '[]'
    )''')
    cur.execute('PRAGMA table_info(vps)')
    columns = [c[1] for c in cur.fetchall()]
    if 'os_version' not in columns:
        cur.execute("ALTER TABLE vps ADD COLUMN os_version TEXT DEFAULT 'ubuntu:22.04'")
    cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
    for k, v in [('cpu_threshold','90'),('ram_threshold','90')]:
        cur.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))
    cur.execute('''CREATE TABLE IF NOT EXISTS port_allocations (user_id TEXT PRIMARY KEY, allocated_ports INTEGER DEFAULT 0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS port_forwards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
        vps_container TEXT NOT NULL, vps_port INTEGER NOT NULL,
        host_port INTEGER NOT NULL, created_at TEXT NOT NULL
    )''')
    conn.commit(); conn.close()

def get_setting(key: str, default: Any = None):
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT value FROM settings WHERE key=?',(key,))
    row=cur.fetchone(); conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn=get_db(); cur=conn.cursor()
    cur.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,value))
    conn.commit(); conn.close()

def get_vps_data() -> Dict[str,List[Dict]]:
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT * FROM vps')
    rows=cur.fetchall(); conn.close()
    data={}
    for row in rows:
        uid=row['user_id']
        if uid not in data: data[uid]=[]
        vps=dict(row)
        vps['shared_with']=json.loads(vps['shared_with'])
        vps['suspension_history']=json.loads(vps['suspension_history'])
        vps['suspended']=bool(vps['suspended'])
        vps['whitelisted']=bool(vps['whitelisted'])
        vps['os_version']=vps.get('os_version','ubuntu:22.04')
        data[uid].append(vps)
    return data

def get_admins() -> List[str]:
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT user_id FROM admins')
    rows=cur.fetchall(); conn.close()
    return [r['user_id'] for r in rows]

def save_vps_data():
    conn=get_db(); cur=conn.cursor()
    for uid, vlist in vps_data.items():
        for vps in vlist:
            sw=json.dumps(vps['shared_with'])
            sh=json.dumps(vps['suspension_history'])
            si=1 if vps['suspended'] else 0
            wi=1 if vps.get('whitelisted',False) else 0
            ov=vps.get('os_version','ubuntu:22.04')
            ca=vps.get('created_at',datetime.now().isoformat())
            if 'id' not in vps or vps['id'] is None:
                cur.execute('''INSERT INTO vps (user_id,container_name,ram,cpu,storage,config,os_version,status,suspended,whitelisted,created_at,shared_with,suspension_history)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (uid,vps['container_name'],vps['ram'],vps['cpu'],vps['storage'],vps['config'],ov,vps['status'],si,wi,ca,sw,sh))
                vps['id']=cur.lastrowid
            else:
                cur.execute('''UPDATE vps SET user_id=?,ram=?,cpu=?,storage=?,config=?,os_version=?,status=?,suspended=?,whitelisted=?,shared_with=?,suspension_history=?
                               WHERE id=?''',
                            (uid,vps['ram'],vps['cpu'],vps['storage'],vps['config'],ov,vps['status'],si,wi,sw,sh,vps['id']))
    conn.commit(); conn.close()

def save_admin_data():
    conn=get_db(); cur=conn.cursor()
    cur.execute('DELETE FROM admins')
    for aid in admin_data['admins']:
        cur.execute('INSERT INTO admins(user_id) VALUES(?)',(aid,))
    conn.commit(); conn.close()

# ──────────────────────────────────────────────
#  PORT FORWARDING HELPERS
# ──────────────────────────────────────────────
def get_user_allocation(uid:str)->int:
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT allocated_ports FROM port_allocations WHERE user_id=?',(uid,))
    r=cur.fetchone(); conn.close(); return r[0] if r else 0

def get_user_used_ports(uid:str)->int:
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT COUNT(*) FROM port_forwards WHERE user_id=?',(uid,))
    r=cur.fetchone(); conn.close(); return r[0]

def allocate_ports(uid:str,amount:int):
    conn=get_db(); cur=conn.cursor()
    cur.execute('INSERT OR REPLACE INTO port_allocations(user_id,allocated_ports) VALUES(?,COALESCE((SELECT allocated_ports FROM port_allocations WHERE user_id=?),0)+?)',(uid,uid,amount))
    conn.commit(); conn.close()

def deallocate_ports(uid:str,amount:int):
    conn=get_db(); cur=conn.cursor()
    cur.execute('UPDATE port_allocations SET allocated_ports=MAX(0,allocated_ports-?) WHERE user_id=?',(amount,uid))
    conn.commit(); conn.close()

def get_available_host_port()->Optional[int]:
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT host_port FROM port_forwards')
    used={r[0] for r in cur.fetchall()}; conn.close()
    for _ in range(100):
        p=random.randint(20000,50000)
        if p not in used: return p
    return None

async def create_port_forward(uid:str,container:str,vps_port:int)->Optional[int]:
    hp=get_available_host_port()
    if not hp: return None
    try:
        await execute_lxc(f"lxc config device add {container} tcp_proxy_{hp} proxy listen=tcp:0.0.0.0:{hp} connect=tcp:127.0.0.1:{vps_port}")
        await execute_lxc(f"lxc config device add {container} udp_proxy_{hp} proxy listen=udp:0.0.0.0:{hp} connect=udp:127.0.0.1:{vps_port}")
        conn=get_db(); cur=conn.cursor()
        cur.execute('INSERT INTO port_forwards(user_id,vps_container,vps_port,host_port,created_at) VALUES(?,?,?,?,?)',
                    (uid,container,vps_port,hp,datetime.now().isoformat()))
        conn.commit(); conn.close(); return hp
    except Exception as e:
        logger.error(f"Port forward failed: {e}"); return None

async def remove_port_forward(fid:int,is_admin:bool=False)->tuple:
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT user_id,vps_container,host_port FROM port_forwards WHERE id=?',(fid,))
    row=cur.fetchone()
    if not row: conn.close(); return False,None
    uid,container,hp=row
    try:
        await execute_lxc(f"lxc config device remove {container} tcp_proxy_{hp}")
        await execute_lxc(f"lxc config device remove {container} udp_proxy_{hp}")
        cur.execute('DELETE FROM port_forwards WHERE id=?',(fid,))
        conn.commit(); conn.close(); return True,uid
    except Exception as e:
        logger.error(f"Remove port forward {fid}: {e}"); conn.close(); return False,None

def get_user_forwards(uid:str)->List[Dict]:
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT * FROM port_forwards WHERE user_id=? ORDER BY created_at DESC',(uid,))
    rows=cur.fetchall(); conn.close(); return [dict(r) for r in rows]

async def recreate_port_forwards(container:str)->int:
    count=0
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT vps_port,host_port FROM port_forwards WHERE vps_container=?',(container,))
    rows=cur.fetchall()
    for r in rows:
        vp,hp=r['vps_port'],r['host_port']
        try:
            await execute_lxc(f"lxc config device add {container} tcp_proxy_{hp} proxy listen=tcp:0.0.0.0:{hp} connect=tcp:127.0.0.1:{vp}")
            await execute_lxc(f"lxc config device add {container} udp_proxy_{hp} proxy listen=udp:0.0.0.0:{hp} connect=udp:127.0.0.1:{vp}")
            logger.info(f"Re-added port forward {hp}→{vp} for {container}"); count+=1
        except Exception as e:
            logger.error(f"Re-add port forward {hp}→{vp} for {container}: {e}")
    conn.close(); return count

# ──────────────────────────────────────────────
#  INIT
# ──────────────────────────────────────────────
init_db()
vps_data   = get_vps_data()
admin_data = {'admins': get_admins()}
CPU_THRESHOLD = int(get_setting('cpu_threshold',90))
RAM_THRESHOLD = int(get_setting('ram_threshold',90))

# ──────────────────────────────────────────────
#  BOT SETUP
# ──────────────────────────────────────────────
intents                = discord.Intents.default()
intents.message_content = True
intents.members        = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
resource_monitor_active = True

# ──────────────────────────────────────────────
#  PREMIUM EMBED BUILDER  ⚡ Ultra Design
# ──────────────────────────────────────────────
BOT_ICON  = "https://i.postimg.cc/XYBHrJVb/Gemini-Generated-Image-p2k9nsp2k9nsp2k9.png"

# ── Visual language tokens ──────────────────────
DIV   = "═" * 32          # heavy separator
DASH  = "─" * 28          # light separator
SPARK = "⚡"
BULLET = "›"

def _ts() -> str:
    return datetime.now().strftime('%d %b %Y  •  %H:%M UTC')

def truncate(text:str, limit:int=4096)->str:
    if not text: return text
    return text if len(text)<=limit else text[:limit-3]+"…"

def build_embed(title:str, description:str="", color:int=Colors.PRIMARY,
                thumbnail:bool=True) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(f"{title}", 256),
        description=truncate(description, 4096),
        color=color
    )
    if thumbnail:
        embed.set_thumbnail(url=BOT_ICON)
    embed.set_footer(
        text=f"{BOT_NAME}  ⚡  {_ts()}",
        icon_url=BOT_ICON
    )
    return embed

def field(embed:discord.Embed, name:str, value:str, inline:bool=False) -> discord.Embed:
    embed.add_field(
        name=truncate(name, 256),
        value=truncate(value, 1024),
        inline=inline
    )
    return embed

# ── Typed helpers ──────────────────────────────
def success_embed(title:str, desc:str="") -> discord.Embed:
    return build_embed(f"✅  {title}", desc, Colors.SUCCESS)

def error_embed(title:str, desc:str="") -> discord.Embed:
    return build_embed(f"❌  {title}", desc, Colors.ERROR)

def info_embed(title:str, desc:str="") -> discord.Embed:
    return build_embed(f"💡  {title}", desc, Colors.INFO)

def warn_embed(title:str, desc:str="") -> discord.Embed:
    return build_embed(f"⚠️  {title}", desc, Colors.WARNING)

def gold_embed(title:str, desc:str="") -> discord.Embed:
    return build_embed(f"👑  {title}", desc, Colors.GOLD)

# Legacy aliases for compatibility
def create_embed(title, description="", color=Colors.PRIMARY):
    return build_embed(title, description, color)
def add_field(embed, name, value, inline=False):
    return field(embed, name, value, inline)
def create_success_embed(title, description=""):
    return success_embed(title, description)
def create_error_embed(title, description=""):
    return error_embed(title, description)
def create_info_embed(title, description=""):
    return info_embed(title, description)
def create_warning_embed(title, description=""):
    return warn_embed(title, description)

# ──────────────────────────────────────────────
#  STATUS BADGE  &  PROGRESS HELPERS
# ──────────────────────────────────────────────
def status_badge(vps:dict) -> str:
    s   = vps.get('status','unknown')
    sus = vps.get('suspended',False)
    wl  = vps.get('whitelisted',False)
    if sus:
        badge = "🔒 `SUSPENDED`"
    elif s == 'running':
        badge = "🟢 `RUNNING`"
    elif s == 'stopped':
        badge = "🔴 `STOPPED`"
    else:
        badge = f"⚪ `{s.upper()}`"
    if wl: badge += "  🛡️ `WHITELISTED`"
    return badge

def status_color(vps:dict) -> int:
    if vps.get('suspended'): return Colors.SUSPENDED
    s = vps.get('status','unknown')
    return Colors.RUNNING if s=='running' else Colors.STOPPED if s=='stopped' else Colors.MUTED

def progress_bar(pct:float, width:int=12) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(pct / 100 * width)
    bar    = "█" * filled + "░" * (width - filled)
    if pct < 50:
        dot = "🟢"
    elif pct < 80:
        dot = "🟡"
    else:
        dot = "🔴"
    return f"{dot} `{bar}` **{pct:.1f}%**"

def mini_bar(done:int, total:int, width:int=14) -> str:
    """Compact progress bar for multi-step operations."""
    filled = round(width * done / total) if total else 0
    pct    = round(100 * done / total)   if total else 0
    bar    = "▰" * filled + "▱" * (width - filled)
    return f"`{bar}` **{pct}%**"

def step_list(steps:list, current:int) -> str:
    """Render a numbered step checklist."""
    lines = []
    for i, (name, _) in enumerate(steps):
        if i < current:
            lines.append(f"  ✅  ~~{name}~~")
        elif i == current:
            lines.append(f"  ⚙️  **{name}**")
        else:
            lines.append(f"  ⬜  {name}")
    return "\n".join(lines)

def done_steps(steps:list) -> str:
    return "\n".join(f"  ✅  ~~{name}~~" for name, _ in steps)

# ──────────────────────────────────────────────
#  ADMIN CHECKS
# ──────────────────────────────────────────────
def is_admin():
    async def predicate(ctx):
        uid=str(ctx.author.id)
        if uid==str(MAIN_ADMIN_ID) or uid in admin_data.get("admins",[]):
            return True
        raise commands.CheckFailure("🔒 You need **Admin** permission to use this command.")
    return commands.check(predicate)

def is_main_admin():
    async def predicate(ctx):
        if str(ctx.author.id)==str(MAIN_ADMIN_ID): return True
        raise commands.CheckFailure("👑 Only the **Main Admin** can use this command.")
    return commands.check(predicate)

# ──────────────────────────────────────────────
#  LXC EXECUTION
# ──────────────────────────────────────────────
async def execute_lxc(command:str, timeout:int=120):
    try:
        cmd=shlex.split(command)
        proc=await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill(); await proc.wait()
            raise asyncio.TimeoutError(f"Command timed out after {timeout}s")
        if proc.returncode!=0:
            err=stderr.decode().strip() if stderr else "Command failed"
            raise Exception(err)
        return stdout.decode().strip() if stdout else True
    except asyncio.TimeoutError as te:
        logger.error(f"LXC timeout: {command} — {te}"); raise
    except Exception as e:
        logger.error(f"LXC Error: {command} — {e}"); raise

async def apply_lxc_config(container:str):
    try:
        await execute_lxc(f"lxc config set {container} security.nesting true")
        await execute_lxc(f"lxc config set {container} security.privileged true")
        await execute_lxc(f"lxc config set {container} security.syscalls.intercept.mknod true")
        await execute_lxc(f"lxc config set {container} security.syscalls.intercept.setxattr true")
        try:
            await execute_lxc(f"lxc config device add {container} fuse unix-char path=/dev/fuse")
        except Exception as e:
            if "already exists" not in str(e).lower(): raise
        await execute_lxc(f"lxc config set {container} linux.kernel_modules overlay,loop,nf_nat,ip_tables,ip6_tables,netlink_diag,br_netfilter")
        raw="""lxc.apparmor.profile = unconfined\nlxc.cgroup.devices.allow = a\nlxc.cap.drop =\nlxc.mount.auto = proc:rw sys:rw cgroup:rw\n"""
        await execute_lxc(f"lxc config set {container} raw.lxc '{raw}'")
        logger.info(f"Applied LXC config to {container}")
    except Exception as e:
        logger.error(f"LXC config failed for {container}: {e}")
        logger.warning(f"Continuing without full config for {container}")

async def apply_internal_permissions(container:str):
    try:
        await asyncio.sleep(5)
        cmds=[
            "mkdir -p /etc/sysctl.d/",
            "echo 'net.ipv4.ip_unprivileged_port_start=0' > /etc/sysctl.d/99-custom.conf",
            "echo 'net.ipv4.ping_group_range=0 2147483647' >> /etc/sysctl.d/99-custom.conf",
            "echo 'fs.inotify.max_user_watches=524288' >> /etc/sysctl.d/99-custom.conf",
            "sysctl -p /etc/sysctl.d/99-custom.conf || true"
        ]
        for cmd in cmds:
            try: await execute_lxc(f'lxc exec {container} -- bash -c "{cmd}"')
            except Exception as e: logger.warning(f"Internal perm cmd failed in {container}: {cmd} — {e}")
        logger.info(f"Applied internal permissions to {container}")
    except Exception as e:
        logger.error(f"Internal permissions failed for {container}: {e}")

# ──────────────────────────────────────────────
#  VPS ROLE
# ──────────────────────────────────────────────
async def get_or_create_vps_role(guild:discord.Guild):
    global VPS_USER_ROLE_ID
    if VPS_USER_ROLE_ID:
        role=guild.get_role(VPS_USER_ROLE_ID)
        if role: return role
    role=discord.utils.get(guild.roles,name=f"{BOT_NAME} VPS User")
    if role: VPS_USER_ROLE_ID=role.id; return role
    try:
        role=await guild.create_role(
            name=f"{BOT_NAME} VPS User",
            color=discord.Color.from_rgb(108,99,255),
            reason=f"{BOT_NAME} VPS User role",
            permissions=discord.Permissions.none()
        )
        VPS_USER_ROLE_ID=role.id
        logger.info(f"Created role: {role.name} ({role.id})")
        return role
    except Exception as e:
        logger.error(f"Failed to create VPS role: {e}"); return None

# ──────────────────────────────────────────────
#  RESOURCE MONITORING
# ──────────────────────────────────────────────
def get_cpu_usage()->float:
    try:
        if shutil.which("mpstat"):
            r=subprocess.run(['mpstat','1','1'],capture_output=True,text=True)
            for line in r.stdout.split('\n'):
                if 'all' in line and '%' in line:
                    return 100.0-float(line.split()[-1])
        else:
            r=subprocess.run(['top','-bn1'],capture_output=True,text=True)
            for line in r.stdout.split('\n'):
                if '%Cpu(s):' in line:
                    p=line.split()
                    return float(p[1])+float(p[3])+float(p[5])+float(p[9])+float(p[11])+float(p[13])+float(p[15])
        return 0.0
    except Exception as e: logger.error(f"CPU usage error: {e}"); return 0.0

def get_ram_usage()->float:
    try:
        r=subprocess.run(['free','-m'],capture_output=True,text=True)
        lines=r.stdout.splitlines()
        if len(lines)>1:
            m=lines[1].split()
            return (int(m[2])/int(m[1])*100) if int(m[1])>0 else 0.0
        return 0.0
    except Exception as e: logger.error(f"RAM usage error: {e}"); return 0.0

def get_uptime()->str:
    try:
        r=subprocess.run(['uptime'],capture_output=True,text=True)
        return r.stdout.strip()
    except: return "Unknown"

def resource_monitor():
    global resource_monitor_active
    backup_interval=3600; last_backup=time.time()
    while resource_monitor_active:
        try:
            cpu=get_cpu_usage(); ram=get_ram_usage()
            logger.info(f"Host — CPU: {cpu:.1f}%  RAM: {ram:.1f}%")
            if cpu>CPU_THRESHOLD or ram>RAM_THRESHOLD:
                logger.warning(f"Resource thresholds exceeded — CPU:{cpu:.1f}% RAM:{ram:.1f}%")
            if time.time()-last_backup>backup_interval:
                bn=f"vps_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                try:
                    shutil.copy('vps.db',bn)
                    if os.path.exists('vps.db-wal'): shutil.copy('vps.db-wal',f"{bn}-wal")
                    if os.path.exists('vps.db-shm'): shutil.copy('vps.db-shm',f"{bn}-shm")
                    logger.info(f"DB backup: {bn}"); last_backup=time.time()
                except Exception as e: logger.error(f"Backup failed: {e}")
            time.sleep(60)
        except Exception as e: logger.error(f"Monitor error: {e}"); time.sleep(60)

monitor_thread=threading.Thread(target=resource_monitor,daemon=True)
monitor_thread.start()

# ──────────────────────────────────────────────
#  CONTAINER STATS
# ──────────────────────────────────────────────
async def get_container_status(name:str)->str:
    try:
        proc=await asyncio.create_subprocess_exec("lxc","info",name,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await proc.communicate()
        for line in stdout.decode().splitlines():
            if line.startswith("Status: "): return line.split(": ",1)[1].strip().lower()
        return "unknown"
    except: return "unknown"

async def get_container_cpu_pct(name:str)->float:
    """Read CPU usage from the container's cgroup — reflects dedicated vCPU allocation."""
    try:
        # Try cgroup v2 first (cpu.stat)
        proc=await asyncio.create_subprocess_exec("lxc","exec",name,"--","bash","-c",
            "cat /sys/fs/cgroup/cpu.stat 2>/dev/null || cat /sys/fs/cgroup/cpu/cpuacct.usage 2>/dev/null",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await proc.communicate()
        text=stdout.decode().strip()
        # cgroup v2: usage_usec line
        for line in text.splitlines():
            if line.startswith("usage_usec"):
                usage1=int(line.split()[1])
                await asyncio.sleep(0.5)
                proc2=await asyncio.create_subprocess_exec("lxc","exec",name,"--","bash","-c",
                    "cat /sys/fs/cgroup/cpu.stat",
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                stdout2,_=await proc2.communicate()
                for l2 in stdout2.decode().splitlines():
                    if l2.startswith("usage_usec"):
                        usage2=int(l2.split()[1])
                        delta_us=(usage2-usage1)
                        # get number of allocated CPUs
                        proc3=await asyncio.create_subprocess_exec("lxc","exec",name,"--","bash","-c",
                            "nproc 2>/dev/null || echo 1",
                            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                        s3,_=await proc3.communicate()
                        ncpu=max(1,int(s3.decode().strip() or 1))
                        pct=(delta_us/(500000.0*ncpu))*100.0
                        return round(min(pct,100.0),1)
        # cgroup v1: cpuacct.usage (nanoseconds)
        if text.isdigit():
            usage1=int(text)
            await asyncio.sleep(0.5)
            proc2=await asyncio.create_subprocess_exec("lxc","exec",name,"--","bash","-c",
                "cat /sys/fs/cgroup/cpu/cpuacct.usage",
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout2,_=await proc2.communicate()
            usage2=int(stdout2.decode().strip())
            delta_ns=usage2-usage1
            proc3=await asyncio.create_subprocess_exec("lxc","exec",name,"--","bash","-c",
                "nproc 2>/dev/null || echo 1",
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            s3,_=await proc3.communicate()
            ncpu=max(1,int(s3.decode().strip() or 1))
            pct=(delta_ns/(500000000.0*ncpu))*100.0
            return round(min(pct,100.0),1)
        # fallback: top inside container
        proc=await asyncio.create_subprocess_exec("lxc","exec",name,"--","top","-bn1",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await proc.communicate()
        for line in stdout.decode().splitlines():
            if '%Cpu(s):' in line:
                p=line.split()
                return float(p[1])+float(p[3])
        return 0.0
    except: return 0.0

async def get_container_cpu(name:str)->str:
    pct=await get_container_cpu_pct(name)
    return f"{pct:.1f}%"

async def _get_container_ram_bytes(name:str):
    """Returns (used_bytes, limit_bytes) from cgroup — dedicated RAM only."""
    try:
        proc=await asyncio.create_subprocess_exec("lxc","exec",name,"--","bash","-c",
            "cat /sys/fs/cgroup/memory.current 2>/dev/null && echo '---' && "
            "cat /sys/fs/cgroup/memory.max 2>/dev/null || "
            "cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null && echo '---' && "
            "cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await proc.communicate()
        parts=[l.strip() for l in stdout.decode().split('---') if l.strip()]
        if len(parts)>=2:
            used=int(parts[0])
            lim_raw=parts[1]
            # cgroup v2 uses "max" for unlimited
            if lim_raw in ('max','9223372036854771712',''):
                # fall back to free -m for total
                proc2=await asyncio.create_subprocess_exec("lxc","exec",name,"--","free","-b",
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                s2,_=await proc2.communicate()
                lines=s2.decode().splitlines()
                if len(lines)>1:
                    lim=int(lines[1].split()[1])
                else: lim=used
            else:
                lim=int(lim_raw)
            return used,lim
    except: pass
    # final fallback: free -b
    try:
        proc=await asyncio.create_subprocess_exec("lxc","exec",name,"--","free","-b",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await proc.communicate()
        lines=stdout.decode().splitlines()
        if len(lines)>1:
            p=lines[1].split()
            return int(p[2]),int(p[1])
    except: pass
    return 0,0

async def get_container_memory(name:str)->str:
    """Show dedicated RAM usage (cgroup-based)."""
    try:
        used,lim=await _get_container_ram_bytes(name)
        if lim==0: return "Unknown"
        used_mb=used//1048576; lim_mb=lim//1048576
        pct=(used/lim*100) if lim>0 else 0
        return f"{used_mb}/{lim_mb} MB ({pct:.1f}%)"
    except: return "Unknown"

async def get_container_ram_pct(name:str)->float:
    """Return RAM % against dedicated limit (cgroup-based)."""
    try:
        used,lim=await _get_container_ram_bytes(name)
        return round((used/lim*100),1) if lim>0 else 0.0
    except: return 0.0

async def get_container_disk(name:str)->str:
    """Get disk usage for the container's root filesystem (works with overlay/rootfs/LXC mounts)."""
    try:
        proc=await asyncio.create_subprocess_exec("lxc","exec",name,"--","df","-h","/",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await proc.communicate()
        lines=stdout.decode().splitlines()
        # Skip header, grab the last line that ends with ' /'
        for line in reversed(lines):
            p=line.split()
            # df -h / always has the root mount as last field
            if len(p)>=6 and p[5]=='/':
                return f"{p[2]}/{p[1]} ({p[4]})"
            if len(p)>=5 and p[4].endswith('%') and (p[5]=='/' if len(p)>5 else True):
                return f"{p[2]}/{p[1]} ({p[4]})"
        # fallback: just take the second line (first data line after header)
        if len(lines)>1:
            p=lines[1].split()
            if len(p)>=5:
                return f"{p[2]}/{p[1]} ({p[4]})"
        return "Unknown"
    except: return "Unknown"

async def get_container_uptime(name:str)->str:
    try:
        proc=await asyncio.create_subprocess_exec("lxc","exec",name,"--","uptime",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await proc.communicate()
        return stdout.decode().strip() if stdout else "Unknown"
    except: return "Unknown"

# ══════════════════════════════════════════════
#  BOT EVENTS
# ══════════════════════════════════════════════
@bot.event
async def on_ready():
    logger.info(f'{bot.user} is online!')
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.watching, name=f"⚡ {BOT_NAME}")
    )
    logger.info(f"⚡ {BOT_NAME} is ready and shining!")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = error_embed("Missing Argument",
            f"You forgot a required argument.\n"
            f"```\n{PREFIX}help\n```\n› *Browse commands with the help menu.*")
        await ctx.send(embed=embed)
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=error_embed("Invalid Argument",
            "One of your arguments is in the wrong format.\n› Double-check your input and try again."))
    elif isinstance(error, commands.CheckFailure):
        msg=str(error) if str(error) else "You don't have permission to use this command."
        await ctx.send(embed=error_embed("Access Denied", msg))
    elif isinstance(error, discord.NotFound):
        await ctx.send(embed=error_embed("Not Found", "The requested resource was not found."))
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(embed=error_embed("Unexpected Error",
            "Something went wrong on our end. Please try again later."))

# ══════════════════════════════════════════════
#  BASIC COMMANDS
# ══════════════════════════════════════════════
@bot.command(name='ping')
async def ping(ctx):
    lat = round(bot.latency * 1000)
    bar = progress_bar(min(lat / 5, 100))
    embed = success_embed("Pong!  🏓",
        f"Bot is online and responding with blazing speed.\n\n"
        f"**⚡ Gateway Latency**\n{bar}\n› `{lat} ms`")
    await ctx.send(embed=embed)

@bot.command(name='uptime')
async def uptime_cmd(ctx):
    up  = get_uptime()
    cpu = get_cpu_usage()
    ram = get_ram_usage()
    embed = info_embed("Host System Uptime",
        f"Current uptime of the **{BOT_NAME}** host machine.\n"
        f"```\n{up}\n```")
    field(embed, "🖥️  Host CPU", progress_bar(cpu), True)
    field(embed, "🧠  Host RAM", progress_bar(ram), True)
    await ctx.send(embed=embed)

@bot.command(name='thresholds')
@is_admin()
async def thresholds(ctx):
    cpu_bar = progress_bar(CPU_THRESHOLD)
    ram_bar = progress_bar(RAM_THRESHOLD)
    embed = info_embed("Resource Alert Thresholds",
        f"VPS will be flagged when usage exceeds these limits.\n{DIV}")
    field(embed, "🖥️  CPU Threshold",  f"{cpu_bar}\n› `{CPU_THRESHOLD}%` trigger level", True)
    field(embed, "🧠  RAM Threshold",  f"{ram_bar}\n› `{RAM_THRESHOLD}%` trigger level", True)
    await ctx.send(embed=embed)

@bot.command(name='set-threshold')
@is_admin()
async def set_threshold(ctx, cpu:int, ram:int):
    global CPU_THRESHOLD, RAM_THRESHOLD
    if cpu<0 or ram<0:
        await ctx.send(embed=error_embed("Invalid Thresholds","Thresholds must be non-negative integers.")); return
    CPU_THRESHOLD=cpu; RAM_THRESHOLD=ram
    set_setting('cpu_threshold',str(cpu)); set_setting('ram_threshold',str(ram))
    embed = success_embed("Thresholds Updated",
        f"Resource alert limits have been saved.\n{DIV}")
    field(embed, "🖥️  CPU Alert", f"{progress_bar(cpu)}\n› Triggers at `{cpu}%`", True)
    field(embed, "🧠  RAM Alert", f"{progress_bar(ram)}\n› Triggers at `{ram}%`", True)
    await ctx.send(embed=embed)

@bot.command(name='set-status')
@is_admin()
async def set_status(ctx, activity_type:str, *, name:str):
    types={'playing':discord.ActivityType.playing,'watching':discord.ActivityType.watching,
           'listening':discord.ActivityType.listening,'streaming':discord.ActivityType.streaming}
    if activity_type.lower() not in types:
        await ctx.send(embed=error_embed("Invalid Activity Type",
            "Valid types: `playing`, `watching`, `listening`, `streaming`")); return
    await bot.change_presence(activity=discord.Activity(type=types[activity_type.lower()],name=name))
    await ctx.send(embed=success_embed("Status Updated",
        f"Bot presence changed.\n\n› **{activity_type.title()}** `{name}`"))

# ══════════════════════════════════════════════
#  MY VPS
# ══════════════════════════════════════════════
@bot.command(name='myvps')
async def my_vps(ctx):
    uid   = str(ctx.author.id)
    vlist = vps_data.get(uid, [])
    if not vlist:
        embed = error_embed("No VPS Found",
            f"You don't have any VPS provisioned yet.\n{DIV}\n"
            f"› Contact an admin to get started!\n"
            f"› Use `{PREFIX}help` to explore available commands.")
        await ctx.send(embed=embed); return

    run_count  = sum(1 for v in vlist if v.get('status') == 'running' and not v.get('suspended'))
    sus_count  = sum(1 for v in vlist if v.get('suspended'))
    stop_count = len(vlist) - run_count - sus_count
    run_pct    = run_count / len(vlist) * 100 if vlist else 0

    embed = info_embed(
        f"{ctx.author.display_name}'s VPS Fleet",
        f"You have **{len(vlist)}** VPS instance(s) provisioned on **{BOT_NAME}**.\n{DIV}\n"
        f"🟢 `{run_count}` Running  ·  🔴 `{stop_count}` Stopped  ·  🔒 `{sus_count}` Suspended"
    )
    field(embed, "📊  Fleet Health", progress_bar(run_pct), False)
    for i, vps in enumerate(vlist, 1):
        cfg  = vps.get('config','Custom')
        os_v = vps.get('os_version','ubuntu:22.04')
        badge = status_badge(vps)
        val = (
            f"{badge}\n"
            f"› **Container:** `{vps['container_name']}`\n"
            f"› **Config:** {cfg}\n"
            f"› **OS:** `{os_v}`"
        )
        field(embed, f"🖥️  VPS #{i}", val, True)
    field(embed, "🎮  Management",
        f"`{PREFIX}manage` — Open interactive VPS control panel\n"
        f"`{PREFIX}ports` — Manage port forwards", False)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════
#  LXC LIST
# ══════════════════════════════════════════════
@bot.command(name='lxc-list')
@is_admin()
async def lxc_list(ctx):
    try:
        result = await execute_lxc("lxc list")
        total  = sum(len(v) for v in vps_data.values())
        run    = sum(1 for vl in vps_data.values() for v in vl if v.get('status')=='running' and not v.get('suspended'))
        run_pct = run / total * 100 if total else 0
        embed  = info_embed("LXC Container Registry",
            f"Live container list from the host.\n{DIV}\n```\n{result}\n```")
        field(embed, "📊  Running Containers", f"{progress_bar(run_pct)}\n› `{run}` / `{total}` running", False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=error_embed("LXC Error", str(e)))

# ══════════════════════════════════════════════
#  OS SELECT VIEW (CREATE VPS)
# ══════════════════════════════════════════════
class OSSelectView(discord.ui.View):
    def __init__(self, ram:int, cpu:int, disk:int, user:discord.Member, ctx):
        super().__init__(timeout=300)
        self.ram=ram; self.cpu=cpu; self.disk=disk; self.user=user; self.ctx=ctx
        self.select = discord.ui.Select(
            placeholder="🖥️  Choose an Operating System…",
            options=[discord.SelectOption(
                label=o["label"], value=o["value"], emoji=o.get("emoji")
            ) for o in OS_OPTIONS]
        )
        self.select.callback = self.select_os
        self.add_item(self.select)

    async def select_os(self, interaction:discord.Interaction):
        if str(interaction.user.id) != str(self.ctx.author.id):
            await interaction.response.send_message(
                embed=error_embed("Access Denied","Only the admin who ran this command can choose."),
                ephemeral=True); return
        os_version = self.select.values[0]
        self.select.disabled = True

        STEPS = [
            ("Initializing container",   "lxc init"),
            ("Allocating resources",     "limits"),
            ("Configuring disk",         "disk"),
            ("Applying security config", "security"),
            ("Booting container",        "start"),
            ("Applying permissions",     "permissions"),
            ("Setting up port forwards", "ports"),
            ("Finalizing",               "saving"),
        ]
        TOTAL = len(STEPS)

        def progress_embed(step_idx:int) -> discord.Embed:
            pct = round(100 * step_idx / TOTAL)
            # Color ramps from INFO → SUCCESS as it fills
            color = Colors.SUCCESS if pct == 100 else Colors.INFO
            embed = build_embed(
                "⚡  Provisioning VPS",
                f"**Owner:** {self.user.mention}  ·  **OS:** `{os_version}`\n"
                f"**Resources:** `{self.ram}GB RAM`  ·  `{self.cpu} vCPU`  ·  `{self.disk}GB Disk`\n\n"
                f"{mini_bar(step_idx, TOTAL)}\n\n"
                f"{step_list(STEPS, step_idx)}",
                color
            )
            return embed

        await interaction.response.edit_message(embed=progress_embed(0), view=self)

        async def update(step_idx:int):
            try:
                await interaction.edit_original_response(embed=progress_embed(step_idx))
            except Exception: pass

        uid = str(self.user.id)
        if uid not in vps_data: vps_data[uid] = []
        count     = len(vps_data[uid]) + 1
        container = f"{BOT_NAME.lower().replace(' ','-')}-vps-{uid}-{count}"
        ram_mb    = self.ram * 1024
        try:
            await execute_lxc(f"lxc init {os_version} {container} -s {DEFAULT_STORAGE_POOL}")
            await update(1)
            await execute_lxc(f"lxc config set {container} limits.memory {ram_mb}MB")
            await execute_lxc(f"lxc config set {container} limits.cpu {self.cpu}")
            await update(2)
            try:
                await execute_lxc(f"lxc config device add {container} root disk pool={DEFAULT_STORAGE_POOL} path=/ size={self.disk}GB")
            except Exception as e:
                if "already exists" in str(e).lower():
                    await execute_lxc(f"lxc config device set {container} root size={self.disk}GB")
                else: raise
            await update(3)
            await apply_lxc_config(container)
            await update(4)
            await execute_lxc(f"lxc start {container}")
            await update(5)
            await apply_internal_permissions(container)
            await update(6)
            await recreate_port_forwards(container)
            await update(7)

            cfg  = f"{self.ram}GB RAM / {self.cpu} vCPU / {self.disk}GB Disk"
            info = {
                "container_name":container, "ram":f"{self.ram}GB", "cpu":str(self.cpu),
                "storage":f"{self.disk}GB", "config":cfg, "os_version":os_version,
                "status":"running", "suspended":False, "whitelisted":False,
                "suspension_history":[], "created_at":datetime.now().isoformat(),
                "shared_with":[], "id":None
            }
            vps_data[uid].append(info); save_vps_data()
            if self.ctx.guild:
                role = await get_or_create_vps_role(self.ctx.guild)
                if role:
                    try: await self.user.add_roles(role, reason=f"{BOT_NAME} VPS granted")
                    except discord.Forbidden: logger.warning(f"Failed to assign VPS role to {self.user.name}")

            # Done — 100% green
            done_embed = build_embed(
                "⚡  Provisioning VPS",
                f"**Owner:** {self.user.mention}  ·  **OS:** `{os_version}`\n"
                f"**Resources:** `{self.ram}GB RAM`  ·  `{self.cpu} vCPU`  ·  `{self.disk}GB Disk`\n\n"
                f"{mini_bar(TOTAL, TOTAL)}\n\n"
                f"{done_steps(STEPS)}",
                Colors.SUCCESS
            )
            try: await interaction.edit_original_response(embed=done_embed, view=self)
            except Exception: pass

            # ── Detailed success embed ──────────────────────
            embed = build_embed("🎉  VPS Deployed Successfully!", "", Colors.SUCCESS)
            field(embed, "👤  Owner",      self.user.mention,       True)
            field(embed, "🔢  VPS Number", f"**#{count}**",         True)
            field(embed, "📦  Container",  f"`{container}`",        True)
            field(embed, "💾  Resources",
                f"› **RAM:** `{self.ram}GB`\n"
                f"› **CPU:** `{self.cpu} vCPU`\n"
                f"› **Disk:** `{self.disk}GB`", True)
            field(embed, "🐧  OS",         f"`{os_version}`",       True)
            field(embed, "📅  Provisioned",
                f"`{datetime.now().strftime('%d %b %Y  %H:%M')}`",  True)
            field(embed, "⚡  Capabilities",
                "Docker-Ready  ·  Nesting  ·  Privileged  ·  FUSE  ·  Ports from 0", False)
            field(embed, "📝  Tip",
                "Run `sudo resize2fs /` inside the VPS if disk expansion is needed.", False)
            await interaction.followup.send(embed=embed)

            # ── DM the user ────────────────────────────────
            dm = build_embed("🚀  Your New VPS is Ready!", "", Colors.SUCCESS)
            field(dm, "📡  Platform",  f"**{BOT_NAME}**", True)
            field(dm, "📦  Container", f"`{container}`",  True)
            field(dm, "💾  Config",
                f"› **RAM:** `{self.ram}GB`\n"
                f"› **CPU:** `{self.cpu} vCPU`\n"
                f"› **Disk:** `{self.disk}GB`\n"
                f"› **OS:** `{os_version}`\n"
                f"› **Status:** 🟢 Running", False)
            field(dm, "🎮  Quick Access",
                f"› `{PREFIX}manage` — Start / Stop / SSH / Stats\n"
                f"› `{PREFIX}myvps` — View all your instances\n"
                f"› Contact admin for upgrades or issues", False)
            field(dm, "🔐  Security Notice",
                "You have **full root access**. Back up your data regularly and avoid resource abuse.", False)
            try: await self.user.send(embed=dm)
            except discord.Forbidden:
                await self.ctx.send(embed=info_embed("DM Blocked",
                    f"Could not notify {self.user.mention}. Ask them to enable DMs from server members."))
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Provisioning Failed",
                f"An error occurred during VPS setup.\n```\n{str(e)}\n```"))

@bot.command(name='create')
@is_admin()
async def create_vps(ctx, ram:int, cpu:int, disk:int, user:discord.Member):
    if ram<=0 or cpu<=0 or disk<=0:
        await ctx.send(embed=error_embed("Invalid Specs","RAM, CPU, and Disk must be positive integers.")); return
    embed = info_embed(
        "VPS Provisioning Wizard",
        f"Configure and deploy a new VPS for {user.mention}.\n{DIV}\n"
        f"**💾 RAM:** `{ram}GB`\n"
        f"**⚙️ CPU:** `{cpu} vCPU`\n"
        f"**🗄️ Disk:** `{disk}GB`\n\n"
        f"*Select an OS from the dropdown below to start the deployment.*"
    )
    view = OSSelectView(ram, cpu, disk, user, ctx)
    await ctx.send(embed=embed, view=view)

# ══════════════════════════════════════════════
#  REINSTALL OS SELECT
# ══════════════════════════════════════════════
class ReinstallOSSelectView(discord.ui.View):
    def __init__(self, parent_view, container:str, owner_id:str, actual_idx:int, ram_gb:int, cpu:int, storage_gb:int):
        super().__init__(timeout=300)
        self.parent_view=parent_view; self.container=container; self.owner_id=owner_id
        self.actual_idx=actual_idx; self.ram_gb=ram_gb; self.cpu=cpu; self.storage_gb=storage_gb
        self.select = discord.ui.Select(
            placeholder="🔄  Select New OS for Reinstall…",
            options=[discord.SelectOption(
                label=o["label"], value=o["value"], emoji=o.get("emoji")
            ) for o in OS_OPTIONS]
        )
        self.select.callback = self.select_os
        self.add_item(self.select)

    async def select_os(self, interaction:discord.Interaction):
        os_version = self.select.values[0]
        self.select.disabled = True
        doing = info_embed("Reinstalling VPS…",
            f"**Container:** `{self.container}`\n"
            f"**New OS:** `{os_version}`\n\n"
            f"⏳ *Please wait, this may take a minute…*")
        await interaction.response.edit_message(embed=doing, view=self)
        ram_mb = self.ram_gb * 1024
        try:
            await execute_lxc(f"lxc init {os_version} {self.container} -s {DEFAULT_STORAGE_POOL}")
            await execute_lxc(f"lxc config set {self.container} limits.memory {ram_mb}MB")
            await execute_lxc(f"lxc config set {self.container} limits.cpu {self.cpu}")
            try:
                await execute_lxc(f"lxc config device add {self.container} root disk pool={DEFAULT_STORAGE_POOL} path=/ size={self.storage_gb}GB")
            except Exception as e:
                if "already exists" in str(e).lower():
                    await execute_lxc(f"lxc config device set {self.container} root size={self.storage_gb}GB")
                else: raise
            await apply_lxc_config(self.container)
            await execute_lxc(f"lxc start {self.container}")
            await apply_internal_permissions(self.container)
            await recreate_port_forwards(self.container)
            tv = vps_data[self.owner_id][self.actual_idx]
            tv["os_version"] = os_version; tv["status"] = "running"
            tv["suspended"]  = False; tv["created_at"] = datetime.now().isoformat()
            tv["config"]     = f"{self.ram_gb}GB RAM / {self.cpu} vCPU / {self.storage_gb}GB Disk"
            save_vps_data()

            embed = success_embed("Reinstall Complete! ✅",
                f"VPS `{self.container}` has been wiped and freshly reinstalled.")
            field(embed, "🐧  New OS",       f"`{os_version}`",                              True)
            field(embed, "💾  Resources",
                f"`{self.ram_gb}GB` RAM · `{self.cpu}` vCPU · `{self.storage_gb}GB` Disk", True)
            field(embed, "⚡  Capabilities", "Docker-Ready · Nesting · Privileged · FUSE",  False)
            field(embed, "📝  Tip",          "Run `sudo resize2fs /` if disk expansion is needed.", False)
            await interaction.followup.send(embed=embed, ephemeral=True); self.stop()
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Reinstall Failed",
                f"```\n{str(e)}\n```"), ephemeral=True)
            self.stop()

# ══════════════════════════════════════════════
#  MANAGE VIEW  (premium interactive panel)
# ══════════════════════════════════════════════
class ManageView(discord.ui.View):
    def __init__(self, user_id:str, vps_list:List, is_shared:bool=False,
                 owner_id:str=None, is_admin:bool=False, actual_index:Optional[int]=None):
        super().__init__(timeout=300)
        self.user_id=user_id; self.vps_list=vps_list[:]
        self.selected_index=None; self.is_shared=is_shared
        self.owner_id=owner_id or user_id; self.is_admin=is_admin; self.actual_index=actual_index
        self.indices=list(range(len(vps_list)))
        if self.is_shared and self.actual_index is None:
            raise ValueError("actual_index required for shared views")
        if len(vps_list) > 1:
            opts = [discord.SelectOption(
                label=f"VPS #{i+1}  —  {v.get('config','Custom')}",
                description=f"{'🔒 SUSPENDED' if v.get('suspended') else '🟢 RUNNING' if v.get('status')=='running' else '🔴 STOPPED'}  ·  {v.get('os_version','?')}",
                value=str(i),
                emoji="🖥️"
            ) for i, v in enumerate(vps_list)]
            self.sel = discord.ui.Select(placeholder="🖥️  Select a VPS to manage…", options=opts)
            self.sel.callback = self.select_vps
            self.add_item(self.sel)
            self.initial_embed = self._build_list_embed()
        else:
            self.selected_index = 0; self.initial_embed = None
            self._add_buttons()

    def _build_list_embed(self):
        embed = info_embed("VPS Control Panel",
            f"You have **{len(self.vps_list)}** VPS instance(s). Select one from the dropdown to manage it.\n{DIV}")
        for i, v in enumerate(self.vps_list, 1):
            val = (
                f"{status_badge(v)}\n"
                f"› {v.get('config','Custom')}\n"
                f"› OS: `{v.get('os_version','?')}`"
            )
            field(embed, f"🖥️  VPS #{i}  `{v['container_name']}`", val, True)
        return embed

    async def get_initial_embed(self):
        if self.initial_embed is not None: return self.initial_embed
        self.initial_embed = await self._vps_embed(self.selected_index)
        return self.initial_embed

    async def _vps_embed(self, idx:int) -> discord.Embed:
        vps       = self.vps_list[idx]
        cn        = vps['container_name']
        lxc_status = await get_container_status(cn)
        cpu_pct   = await get_container_cpu_pct(cn)
        mem       = await get_container_memory(cn)
        ram_pct   = await get_container_ram_pct(cn)
        disk      = await get_container_disk(cn)
        up        = await get_container_uptime(cn)
        color     = status_color(vps)

        owner_line = ""
        if self.is_admin and self.owner_id != self.user_id:
            try:
                u = await bot.fetch_user(int(self.owner_id))
                owner_line = f"\n› 👤 **Owner:** {u.mention}"
            except: owner_line = f"\n› 👤 **Owner ID:** `{self.owner_id}`"

        embed = build_embed(
            f"🖥️  VPS Manager — #{idx+1}",
            f"**Container:** `{cn}`{owner_line}\n{DIV}",
            color
        )
        field(embed, "📊  Status",
            f"{status_badge(vps)}\n"
            f"› **Config:** {vps.get('config','Custom')}\n"
            f"› **OS:** `{vps.get('os_version','ubuntu:22.04')}`", False)
        field(embed, "💻  CPU Usage",  progress_bar(cpu_pct),              True)
        field(embed, "🧠  RAM Usage",  progress_bar(ram_pct)+f"\n› `{mem}`", True)
        field(embed, "💾  Disk",       f"`{disk}`",                        True)
        field(embed, "⏱️  Uptime",     f"```\n{up}\n```",                  False)
        if vps.get('suspended'):
            field(embed, "🔒  Suspension Notice",
                "This VPS has been suspended by an admin.\n"
                "› Contact support to have it reinstated.", False)
        if vps.get('whitelisted'):
            field(embed, "🛡️  Whitelist Status",
                "This VPS is **exempt** from automatic resource suspension.", False)
        field(embed, "🎮  Controls", "Use the buttons below to manage your VPS.", False)
        return embed

    def _add_buttons(self):
        # Row 0 — primary actions
        s = discord.ui.Button(label="Start",      style=discord.ButtonStyle.success,   emoji="▶️", row=0)
        s.callback = lambda i: self.action_callback(i, 'start'); self.add_item(s)

        st = discord.ui.Button(label="Stop",      style=discord.ButtonStyle.secondary, emoji="⏹️", row=0)
        st.callback = lambda i: self.action_callback(i, 'stop'); self.add_item(st)

        ssh = discord.ui.Button(label="SSH Access", style=discord.ButtonStyle.primary, emoji="🔑", row=0)
        ssh.callback = lambda i: self.action_callback(i, 'tmate'); self.add_item(ssh)

        # Row 1 — secondary
        if not (self.is_shared or self.is_admin):
            b = discord.ui.Button(label="Reinstall", style=discord.ButtonStyle.danger, emoji="🔄", row=1)
            b.callback = lambda i: self.action_callback(i, 'reinstall'); self.add_item(b)

        stats = discord.ui.Button(label="Live Stats", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
        stats.callback = lambda i: self.action_callback(i, 'stats'); self.add_item(stats)

        refresh = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔁", row=1)
        refresh.callback = lambda i: self.action_callback(i, 'refresh'); self.add_item(refresh)

    async def select_vps(self, interaction:discord.Interaction):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(
                embed=error_embed("Access Denied","This is not your VPS panel."), ephemeral=True); return
        self.selected_index = int(self.sel.values[0])
        await interaction.response.defer()
        new_embed = await self._vps_embed(self.selected_index)
        self.clear_items(); self._add_buttons()
        await interaction.edit_original_response(embed=new_embed, view=self)

    async def action_callback(self, interaction:discord.Interaction, action:str):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(
                embed=error_embed("Access Denied","This is not your VPS panel."), ephemeral=True); return
        if self.selected_index is None:
            await interaction.response.send_message(
                embed=error_embed("No VPS Selected","Please select a VPS first."), ephemeral=True); return

        aidx = self.actual_index if self.is_shared else self.indices[self.selected_index]
        tvps = vps_data[self.owner_id][aidx]
        sus  = tvps.get('suspended', False)
        cn   = tvps["container_name"]

        if sus and not self.is_admin and action not in ('stats','refresh'):
            await interaction.response.send_message(embed=error_embed("VPS Suspended",
                "Your VPS is suspended. Please contact an admin to unsuspend it."),
                ephemeral=True); return

        # ── LIVE STATS ───────────────────────
        if action == 'stats':
            await interaction.response.defer(ephemeral=True)
            status  = await get_container_status(cn)
            cpu_pct = await get_container_cpu_pct(cn)
            ram_pct = await get_container_ram_pct(cn)
            mem     = await get_container_memory(cn)
            disk    = await get_container_disk(cn)
            up      = await get_container_uptime(cn)
            embed   = info_embed(f"Live Statistics — `{cn}`",
                f"Real-time resource usage snapshot.\n{DIV}")
            field(embed, "🖥️  Status",  f"`{status.upper()}`",                    True)
            field(embed, "⏱️  Uptime",  f"```{up}```",                            True)
            field(embed, "💻  CPU",     progress_bar(cpu_pct),                    True)
            field(embed, "🧠  Memory",  progress_bar(ram_pct)+f"\n`{mem}`",       True)
            field(embed, "💾  Disk",    f"`{disk}`",                              True)
            await interaction.followup.send(embed=embed, ephemeral=True); return

        # ── REFRESH ──────────────────────────
        if action == 'refresh':
            await interaction.response.defer()
            new_embed = await self._vps_embed(self.selected_index)
            await interaction.edit_original_response(embed=new_embed, view=self); return

        # ── REINSTALL ────────────────────────
        if action == 'reinstall':
            if self.is_shared or self.is_admin:
                await interaction.response.send_message(
                    embed=error_embed("Not Allowed","Only the VPS owner can reinstall."), ephemeral=True); return
            if sus:
                await interaction.response.send_message(
                    embed=error_embed("VPS Suspended","Unsuspend before reinstalling."), ephemeral=True); return
            ram_gb = int(tvps['ram'].replace('GB',''))
            cpu    = int(tvps['cpu']); stg = int(tvps['storage'].replace('GB',''))
            conf   = warn_embed("⚠️  Reinstall Confirmation",
                f"**This will permanently wipe all data on `{cn}` and reinstall a fresh OS.**\n\n"
                f"› This action **cannot be undone**.\n"
                f"› **Current Config:** {tvps.get('config','Custom')}")

            class ConfirmReinstall(discord.ui.View):
                def __init__(pvself, pv, cname, oid, aidx, rg, c, sg):
                    super().__init__(timeout=60)
                    pvself.pv=pv; pvself.cname=cname; pvself.oid=oid
                    pvself.aidx=aidx; pvself.rg=rg; pvself.c=c; pvself.sg=sg

                @discord.ui.button(label="Confirm Wipe & Reinstall", style=discord.ButtonStyle.danger, emoji="💣")
                async def confirm(pvself, inter:discord.Interaction, item:discord.ui.Button):
                    await inter.response.defer(ephemeral=True)
                    try:
                        await inter.followup.send(embed=info_embed("Removing Container",
                            f"Force-deleting `{pvself.cname}`…"), ephemeral=True)
                        await execute_lxc(f"lxc delete {pvself.cname} --force")
                        osv = ReinstallOSSelectView(pvself.pv, pvself.cname, pvself.oid, pvself.aidx, pvself.rg, pvself.c, pvself.sg)
                        await inter.followup.send(embed=info_embed("Choose New OS",
                            "Select the operating system for your fresh VPS installation."),
                            view=osv, ephemeral=True)
                    except Exception as e:
                        await inter.followup.send(embed=error_embed("Deletion Failed",
                            f"```\n{e}\n```"), ephemeral=True)

                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
                async def cancel(pvself, inter:discord.Interaction, item:discord.ui.Button):
                    new = await pvself.pv._vps_embed(pvself.pv.selected_index)
                    await inter.response.edit_message(embed=new, view=pvself.pv)

            await interaction.response.send_message(embed=conf,
                view=ConfirmReinstall(self, cn, self.owner_id, aidx, ram_gb, cpu, stg),
                ephemeral=True); return

        await interaction.response.defer(ephemeral=True)

        # ── START ────────────────────────────
        if action == 'start':
            try:
                await execute_lxc(f"lxc start {cn}")
                tvps["status"] = "running"; save_vps_data()
                await apply_internal_permissions(cn)
                readded = await recreate_port_forwards(cn)
                embed = success_embed("VPS Started ▶️",
                    f"Container `{cn}` is now running.\n\n"
                    f"› Re-attached **{readded}** port forward(s).")
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=error_embed("Start Failed",
                    f"```\n{e}\n```"), ephemeral=True)

        # ── STOP ─────────────────────────────
        elif action == 'stop':
            try:
                await execute_lxc(f"lxc stop {cn}", timeout=120)
                tvps["status"] = "stopped"; save_vps_data()
                await interaction.followup.send(embed=success_embed("VPS Stopped ⏹️",
                    f"Container `{cn}` has been gracefully stopped."), ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=error_embed("Stop Failed",
                    f"```\n{e}\n```"), ephemeral=True)

        # ── SSH / TMATE ──────────────────────
        elif action == 'tmate':
            if sus:
                await interaction.followup.send(embed=error_embed("Access Denied",
                    "Cannot SSH into a suspended VPS."), ephemeral=True); return
            await interaction.followup.send(embed=info_embed("Generating SSH Session…",
                "Checking for tmate on the container…"), ephemeral=True)
            try:
                chk = await asyncio.create_subprocess_exec("lxc","exec",cn,"--","which","tmate",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _,__ = await chk.communicate()
                if chk.returncode != 0:
                    await interaction.followup.send(embed=info_embed("Installing tmate",
                        "Running `apt-get install tmate`…"), ephemeral=True)
                    await execute_lxc(f"lxc exec {cn} -- apt-get update -y")
                    await execute_lxc(f"lxc exec {cn} -- apt-get install tmate -y")
                    await interaction.followup.send(embed=success_embed("tmate Installed",
                        "SSH service is ready."), ephemeral=True)
                sname = f"{BOT_NAME.lower().replace(' ','-')}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                await execute_lxc(f"lxc exec {cn} -- tmate -S /tmp/{sname}.sock new-session -d")
                await asyncio.sleep(3)
                sp = await asyncio.create_subprocess_exec("lxc","exec",cn,"--","tmate","-S",
                    f"/tmp/{sname}.sock","display","-p","#{tmate_ssh}",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await sp.communicate()
                ssh_url = stdout.decode().strip() if stdout else None
                if ssh_url:
                    ssh_embed = build_embed("🔑  SSH Access Ready",
                        f"Your temporary SSH session for `{cn}` is active.\n{DIV}",
                        Colors.SUCCESS)
                    field(ssh_embed, "📋  SSH Command",
                        f"```\n{ssh_url}\n```", False)
                    field(ssh_embed, "⚠️  Security Notice",
                        "This link grants **root access**. Never share it publicly.\n"
                        "› The link expires when the session ends.", False)
                    field(ssh_embed, "🏷️  Session ID", f"`{sname}`", True)
                    try:
                        await interaction.user.send(embed=ssh_embed)
                        await interaction.followup.send(embed=success_embed("SSH Session Sent 📬",
                            f"Check your **Direct Messages** for the SSH connection link!\n"
                            f"› Session: `{sname}`"), ephemeral=True)
                    except discord.Forbidden:
                        await interaction.followup.send(embed=error_embed("DMs Disabled",
                            "Enable Direct Messages so we can send you the SSH link."), ephemeral=True)
                else:
                    err = stderr.decode().strip() if stderr else "Unknown error"
                    await interaction.followup.send(embed=error_embed("SSH Failed",
                        f"```\n{err}\n```"), ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=error_embed("SSH Error",
                    f"```\n{e}\n```"), ephemeral=True)

        # Refresh embed after action
        try:
            new_embed = await self._vps_embed(self.selected_index)
            await interaction.edit_original_response(embed=new_embed, view=self)
        except Exception: pass

@bot.command(name='manage')
async def manage_vps(ctx, user:discord.Member=None):
    STEPS = [("Authenticating","a"), ("Fetching VPS list","f"), ("Opening control panel","l")]
    TOTAL = len(STEPS)

    def prog_embed(i, color=Colors.INFO):
        return build_embed("🖥️  Opening VPS Manager",
            f"**User:** {user.mention if user else ctx.author.mention}\n\n"
            f"{mini_bar(i, TOTAL)}\n\n{step_list(STEPS, i)}", color)

    msg = await ctx.send(embed=prog_embed(0))
    async def update(i, color=Colors.INFO):
        try: await msg.edit(embed=prog_embed(i, color))
        except Exception: pass

    if user:
        uc = str(ctx.author.id)
        if uc!=str(MAIN_ADMIN_ID) and uc not in admin_data.get("admins",[]):
            await msg.edit(embed=error_embed("Access Denied","Only admins can manage other users' VPS.")); return
        await update(1)
        uid = str(user.id); vlist = vps_data.get(uid, [])
        await update(2)
        if not vlist:
            await msg.edit(embed=error_embed("No VPS Found", f"{user.mention} doesn't have any VPS.")); return
        done_embed = build_embed("🖥️  Opening VPS Manager",
            f"**User:** {user.mention}\n\n{mini_bar(TOTAL, TOTAL)}\n\n{done_steps(STEPS)}",
            Colors.SUCCESS)
        await msg.edit(embed=done_embed)
        view = ManageView(str(ctx.author.id), vlist, is_admin=True, owner_id=uid)
        await ctx.send(embed=info_embed(f"Managing {user.display_name}'s VPS",
            f"Admin control panel for {user.mention}'s VPS instances.\n{DIV}"), view=view)
    else:
        await update(1)
        uid = str(ctx.author.id); vlist = vps_data.get(uid, [])
        await update(2)
        if not vlist:
            no_vps = build_embed("🖥️  VPS Manager",
                f"**User:** {ctx.author.mention}\n\n"
                f"{mini_bar(0, TOTAL)}\n\n"
                f"❌  **No VPS found!**\n\n"
                f"› You don't have any active VPS.\n"
                f"› Contact an admin to get one provisioned!\n\n"
                f"📖 Use `{PREFIX}help` to explore all commands.",
                Colors.ERROR)
            await msg.edit(embed=no_vps); return
        done_embed = build_embed("🖥️  Opening VPS Manager",
            f"**User:** {ctx.author.mention}\n\n{mini_bar(TOTAL, TOTAL)}\n\n{done_steps(STEPS)}",
            Colors.SUCCESS)
        await msg.edit(embed=done_embed)
        view  = ManageView(uid, vlist)
        embed = await view.get_initial_embed()
        await ctx.send(embed=embed, view=view)

# ══════════════════════════════════════════════
#  LIST ALL
# ══════════════════════════════════════════════
@bot.command(name='list-all')
@is_admin()
async def list_all_vps(ctx):
    total=run=stop=sus=wl=0
    users=len(vps_data); infos=[]; summaries=[]
    for uid, vlist in vps_data.items():
        try: u = await bot.fetch_user(int(uid))
        except: u = None
        urun  = sum(1 for v in vlist if v.get('status')=='running' and not v.get('suspended'))
        ustop = sum(1 for v in vlist if v.get('status')=='stopped')
        usus  = sum(1 for v in vlist if v.get('suspended'))
        uwl   = sum(1 for v in vlist if v.get('whitelisted'))
        total+=len(vlist); run+=urun; stop+=ustop; sus+=usus; wl+=uwl
        name    = u.name if u else f"ID:{uid}"
        mention = u.mention if u else f"`{uid}`"
        summaries.append(
            f"**{name}** ({mention}) — `{len(vlist)}` VPS  "
            f"({urun}🟢 {usus}🔒 {uwl}🛡️)")
        for i, v in enumerate(vlist):
            emoji = "🟢" if v.get('status')=='running' and not v.get('suspended') else "🔒" if v.get('suspended') else "🔴"
            infos.append(f"{emoji} **{name}** — VPS #{i+1}: `{v['container_name']}` — {v.get('config','Custom')}")

    embed = info_embed("Platform Overview",
        f"Global snapshot of all **{BOT_NAME}** deployments.\n{DIV}")
    field(embed, "👥  Community",
        f"› **Users:** `{users}`\n"
        f"› **Admins:** `{len(admin_data.get('admins',[]))+1}`", True)
    field(embed, "🖥️  VPS Fleet",
        f"› **Total:** `{total}`\n"
        f"› 🟢 Running: `{run}`\n"
        f"› 🔴 Stopped: `{stop}`\n"
        f"› 🔒 Suspended: `{sus}`\n"
        f"› 🛡️ Whitelisted: `{wl}`", True)
    run_pct = run / total * 100 if total else 0
    sus_pct = sus / total * 100 if total else 0
    field(embed, "📊  Running",   f"{progress_bar(run_pct)}\n› `{run}` / `{total}`", True)
    field(embed, "🔒  Suspended", f"{progress_bar(sus_pct)}\n› `{sus}` / `{total}`", True)
    await ctx.send(embed=embed)

    if summaries:
        chunks = [summaries[i:i+8] for i in range(0, len(summaries), 8)]
        for idx, chunk in enumerate(chunks, 1):
            emb = info_embed("User Breakdown", "")
            field(emb, f"Users — Page {idx}/{len(chunks)}", "\n".join(chunk), False)
            await ctx.send(embed=emb)

    if infos:
        chunks = [infos[i:i+10] for i in range(0, len(infos), 10)]
        for idx, chunk in enumerate(chunks, 1):
            emb = info_embed(f"VPS Registry — Page {idx}/{len(chunks)}", "")
            field(emb, "All Instances", "\n".join(chunk), False)
            await ctx.send(embed=emb)

# ══════════════════════════════════════════════
#  SHARED VPS
# ══════════════════════════════════════════════
@bot.command(name='manage-shared')
async def manage_shared_vps(ctx, owner:discord.Member, vps_number:int):
    oid = str(owner.id); uid = str(ctx.author.id)
    if oid not in vps_data or vps_number<1 or vps_number>len(vps_data[oid]):
        await ctx.send(embed=error_embed("Invalid VPS","Owner or VPS number not found.")); return
    vps = vps_data[oid][vps_number-1]
    if uid not in vps.get("shared_with",[]):
        await ctx.send(embed=error_embed("Access Denied","You don't have access to this VPS.")); return

    STEPS = [("Verifying access","v"), ("Loading VPS data","l"), ("Opening control panel","o")]
    TOTAL = len(STEPS)
    def prog_embed(i):
        return build_embed("🖥️  Opening Shared VPS",
            f"**VPS #{vps_number}** owned by {owner.mention}\n\n"
            f"{mini_bar(i, TOTAL)}\n\n{step_list(STEPS, i)}", Colors.INFO)

    msg = await ctx.send(embed=prog_embed(0))
    await msg.edit(embed=prog_embed(1))
    view  = ManageView(uid, [vps], is_shared=True, owner_id=oid, actual_index=vps_number-1)
    embed = await view.get_initial_embed()
    await msg.edit(embed=prog_embed(2))
    done_embed = build_embed("🖥️  Opening Shared VPS",
        f"**VPS #{vps_number}** owned by {owner.mention}\n\n"
        f"{mini_bar(TOTAL, TOTAL)}\n\n{done_steps(STEPS)}", Colors.SUCCESS)
    await msg.edit(embed=done_embed)
    await ctx.send(embed=embed, view=view)

@bot.command(name='share-user')
async def share_user(ctx, shared_user:discord.Member, vps_number:int):
    uid = str(ctx.author.id); suid = str(shared_user.id)
    if uid not in vps_data or vps_number<1 or vps_number>len(vps_data[uid]):
        await ctx.send(embed=error_embed("Invalid VPS","Invalid VPS number.")); return
    vps = vps_data[uid][vps_number-1]
    vps.setdefault("shared_with",[])
    if suid in vps["shared_with"]:
        await ctx.send(embed=error_embed("Already Shared",
            f"{shared_user.mention} already has access to this VPS.")); return

    STEPS = [("Verifying VPS","v"), ("Granting access","g"), ("Saving changes","s"), ("Notifying user","n")]
    TOTAL = len(STEPS)
    def prog_embed(i):
        return build_embed("🔓  Sharing VPS Access",
            f"**VPS #{vps_number}** → {shared_user.mention}\n\n"
            f"{mini_bar(i, TOTAL)}\n\n{step_list(STEPS, i)}", Colors.INFO)

    msg = await ctx.send(embed=prog_embed(0))
    async def update(i):
        try: await msg.edit(embed=prog_embed(i))
        except Exception: pass

    await update(1)
    vps["shared_with"].append(suid)
    await update(2)
    save_vps_data()
    await update(3)
    try:
        dm = info_embed("VPS Access Granted 🔓",
            f"{ctx.author.mention} has shared **VPS #{vps_number}** with you!\n\n"
            f"› **Command:** `{PREFIX}manage-shared @{ctx.author.name} {vps_number}`")
        await shared_user.send(embed=dm)
    except discord.Forbidden: pass

    done_embed = build_embed("🔓  VPS Shared Successfully",
        f"**VPS #{vps_number}** → {shared_user.mention}\n\n"
        f"{mini_bar(TOTAL, TOTAL)}\n\n{done_steps(STEPS)}", Colors.SUCCESS)
    try: await msg.edit(embed=done_embed)
    except Exception: pass
    await ctx.send(embed=success_embed("VPS Shared",
        f"**VPS #{vps_number}** has been shared with {shared_user.mention}.\n"
        f"› They can use `{PREFIX}manage-shared @{ctx.author.name} {vps_number}` to access it."))

@bot.command(name='share-ruser')
async def revoke_share(ctx, shared_user:discord.Member, vps_number:int):
    uid = str(ctx.author.id); suid = str(shared_user.id)
    if uid not in vps_data or vps_number<1 or vps_number>len(vps_data[uid]):
        await ctx.send(embed=error_embed("Invalid VPS","Invalid VPS number.")); return
    vps = vps_data[uid][vps_number-1]
    vps.setdefault("shared_with",[])
    if suid not in vps["shared_with"]:
        await ctx.send(embed=error_embed("Not Shared",
            f"{shared_user.mention} doesn't have access to this VPS.")); return

    STEPS = [("Verifying access","v"), ("Revoking permissions","r"), ("Saving changes","s"), ("Notifying user","n")]
    TOTAL = len(STEPS)
    def prog_embed(i):
        return build_embed("🔒  Revoking VPS Access",
            f"**VPS #{vps_number}** ✂ {shared_user.mention}\n\n"
            f"{mini_bar(i, TOTAL)}\n\n{step_list(STEPS, i)}", Colors.WARNING)

    msg = await ctx.send(embed=prog_embed(0))
    async def update(i):
        try: await msg.edit(embed=prog_embed(i))
        except Exception: pass

    await update(1)
    vps["shared_with"].remove(suid)
    await update(2)
    save_vps_data()
    await update(3)
    try:
        await shared_user.send(embed=warn_embed("VPS Access Revoked",
            f"Your access to **VPS #{vps_number}** from {ctx.author.mention} has been **removed**."))
    except discord.Forbidden: pass

    done_embed = build_embed("🔒  Access Revoked",
        f"**VPS #{vps_number}** ✂ {shared_user.mention}\n\n"
        f"{mini_bar(TOTAL, TOTAL)}\n\n{done_steps(STEPS)}", Colors.SUCCESS)
    try: await msg.edit(embed=done_embed)
    except Exception: pass
    await ctx.send(embed=success_embed("Access Revoked",
        f"Removed {shared_user.mention}'s access to **VPS #{vps_number}**."))

# ══════════════════════════════════════════════
#  PORT FORWARDING COMMANDS
# ══════════════════════════════════════════════
@bot.command(name='ports-add-user')
@is_admin()
async def ports_add_user(ctx, amount:int, user:discord.Member):
    if amount<=0:
        await ctx.send(embed=error_embed("Invalid Amount","Amount must be a positive integer.")); return
    uid = str(user.id); allocate_ports(uid, amount)
    total = get_user_allocation(uid)
    embed = success_embed("Port Slots Allocated 🔌",
        f"Granted **{amount}** port forwarding slot(s) to {user.mention}.\n"
        f"› **New Total:** `{total}` slots")
    await ctx.send(embed=embed)
    try:
        dm = info_embed("Port Slots Allocated 🔌",
            f"You've been granted **{amount}** port forwarding slot(s) by an admin.\n"
            f"› **Total:** `{total}` slots\n"
            f"› Use `{PREFIX}ports list` to manage your forwards.")
        await user.send(embed=dm)
    except discord.Forbidden: pass

@bot.command(name='ports-remove-user')
@is_admin()
async def ports_remove_user(ctx, amount:int, user:discord.Member):
    if amount<=0:
        await ctx.send(embed=error_embed("Invalid Amount","Amount must be a positive integer.")); return
    uid = str(user.id); cur = get_user_allocation(uid)
    if amount>cur: amount=cur
    deallocate_ports(uid, amount); remaining = get_user_allocation(uid)
    await ctx.send(embed=success_embed("Port Slots Reduced",
        f"Removed **{amount}** slot(s) from {user.mention}.\n"
        f"› **Remaining:** `{remaining}` slots"))
    try:
        await user.send(embed=warn_embed("Port Quota Reduced",
            f"Your port forwarding quota was reduced by **{amount}** slot(s) by an admin.\n"
            f"› **Remaining:** `{remaining}` slots."))
    except discord.Forbidden: pass

@bot.command(name='ports-revoke')
@is_admin()
async def ports_revoke(ctx, forward_id:int):
    success,uid = await remove_port_forward(forward_id, is_admin=True)
    if success and uid:
        try:
            u = await bot.fetch_user(int(uid))
            await u.send(embed=warn_embed("Port Forward Revoked",
                f"Port forward **ID {forward_id}** has been revoked by an admin."))
        except: pass
        await ctx.send(embed=success_embed("Port Forward Revoked",
            f"Forward ID `{forward_id}` has been removed."))
    else:
        await ctx.send(embed=error_embed("Not Found",
            f"Port forward ID `{forward_id}` was not found."))

@bot.command(name='ports')
async def ports_command(ctx, subcmd:str=None, *args):
    uid   = str(ctx.author.id)
    alloc = get_user_allocation(uid); used = get_user_used_ports(uid); avail = alloc - used

    if subcmd is None:
        bar = progress_bar((used/alloc*100) if alloc>0 else 0)
        embed = info_embed("Port Forwarding Center 🔌",
            f"Manage your TCP/UDP port forwards on **{BOT_NAME}**.\n{DIV}\n"
            f"{bar}\n› `{used}/{alloc}` used  ·  `{avail}` available")
        field(embed, "📋  Commands",
            f"`{PREFIX}ports add <vps_num> <port>` — Forward a port\n"
            f"`{PREFIX}ports list` — View active forwards\n"
            f"`{PREFIX}ports remove <id>` — Remove a forward", False)
        await ctx.send(embed=embed); return

    if subcmd == 'add':
        if len(args) < 2:
            await ctx.send(embed=error_embed("Usage",
                f"`{PREFIX}ports add <vps_number> <vps_port>`")); return
        try:
            vn = int(args[0]); vp = int(args[1])
            if vp<1 or vp>65535: raise ValueError
        except ValueError:
            await ctx.send(embed=error_embed("Invalid Input",
                "VPS number must be a positive integer and port must be 1–65535.")); return
        vlist = vps_data.get(uid, [])
        if vn<1 or vn>len(vlist):
            await ctx.send(embed=error_embed("Invalid VPS",
                f"Use `{PREFIX}myvps` to see your VPS numbers.")); return
        vps = vlist[vn-1]; container = vps['container_name']
        if used >= alloc:
            await ctx.send(embed=error_embed("Quota Exceeded",
                f"You've used all **{alloc}** port slot(s).\n"
                f"› Contact an admin for additional slots.")); return
        hp = await create_port_forward(uid, container, vp)
        if hp:
            embed = success_embed("Port Forward Created ✅",
                f"VPS #{vn} port `{vp}` (TCP & UDP) is now forwarded.")
            field(embed, "🌐  Public Access",  f"`{YOUR_SERVER_IP}:{hp}` → VPS Port `{vp}`", False)
            field(embed, "📊  Quota",          f"`{used+1}/{alloc}` slots used",              True)
            field(embed, "🔗  Protocol",       "TCP **+** UDP both forwarded",                True)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=error_embed("Failed",
                "Could not assign a host port. Try again later."))

    elif subcmd == 'list':
        fwds = get_user_forwards(uid)
        bar  = progress_bar((used/alloc*100) if alloc>0 else 0) if alloc>0 else "`No quota allocated`"
        embed = info_embed("Your Port Forwards",
            f"**Quota:** {bar}\n› `{used}/{alloc}` used\n{DIV}")
        if not fwds:
            field(embed, "📋  Active Forwards",
                f"No active forwards. Use `{PREFIX}ports add` to create one.", False)
        else:
            lines = []
            for f in fwds[:10]:
                vn      = next((i+1 for i,v in enumerate(vps_data.get(uid,[])) if v['container_name']==f['vps_container']),'?')
                created = datetime.fromisoformat(f['created_at']).strftime('%d %b %Y  %H:%M')
                lines.append(
                    f"**ID `{f['id']}`** — VPS #{vn}: `{f['vps_port']}` → `{f['host_port']}`\n"
                    f"  › Created: {created}")
            field(embed, "📋  Active Forwards", "\n".join(lines), False)
            if len(fwds) > 10:
                field(embed, "📝  Note",
                    f"Showing 10 of {len(fwds)}. Remove unused ones with `{PREFIX}ports remove <id>`.", False)
        await ctx.send(embed=embed)

    elif subcmd == 'remove':
        if len(args) < 1:
            await ctx.send(embed=error_embed("Usage",
                f"`{PREFIX}ports remove <forward_id>`")); return
        try: fid = int(args[0])
        except ValueError:
            await ctx.send(embed=error_embed("Invalid ID","Forward ID must be an integer.")); return
        success,_ = await remove_port_forward(fid)
        if success:
            embed = success_embed("Forward Removed",
                f"Port forward `{fid}` has been removed (TCP & UDP).")
            field(embed, "📊  Quota", f"`{used-1}/{alloc}` slots used", True)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=error_embed("Not Found",
                f"No forward with ID `{fid}` found. Use `{PREFIX}ports list`."))
    else:
        await ctx.send(embed=error_embed("Unknown Subcommand",
            f"Use: `add <vps_num> <port>`, `list`, or `remove <id>`"))

# ══════════════════════════════════════════════
#  DELETE VPS
# ══════════════════════════════════════════════
@bot.command(name='delete-vps')
@is_admin()
async def delete_vps(ctx, user:discord.Member, vps_number:int, *, reason:str="No reason provided"):
    uid = str(user.id)
    if uid not in vps_data or vps_number<1 or vps_number>len(vps_data[uid]):
        await ctx.send(embed=error_embed("Invalid VPS","VPS number or user not found.")); return
    vps = vps_data[uid][vps_number-1]; cn = vps["container_name"]

    DEL_STEPS = [
        ("Revoking port forwards",   "ports"),
        ("Force stopping container", "stop"),
        ("Destroying container",     "delete"),
        ("Cleaning up records",      "cleanup"),
        ("Revoking VPS role",        "role"),
        ("Notifying user",           "notify"),
    ]
    TOTAL = len(DEL_STEPS)

    def del_embed(step_idx:int) -> discord.Embed:
        return build_embed(
            "🗑️  Deleting VPS",
            f"**Container:** `{cn}`  ·  **Owner:** {user.mention}\n"
            f"**Reason:** {reason}\n\n"
            f"{mini_bar(step_idx, TOTAL)}\n\n"
            f"{step_list(DEL_STEPS, step_idx)}",
            Colors.ERROR
        )

    msg = await ctx.send(embed=del_embed(0))
    async def update(step_idx:int):
        try: await msg.edit(embed=del_embed(step_idx))
        except Exception: pass

    try:
        conn=get_db(); cur=conn.cursor()
        cur.execute('DELETE FROM port_forwards WHERE vps_container=?',(cn,))
        conn.commit(); conn.close()
        await update(1)
        try: await execute_lxc(f"lxc stop {cn} --force")
        except Exception: pass
        await update(2)
        await execute_lxc(f"lxc delete {cn} --force")
        await update(3)
        del vps_data[uid][vps_number-1]
        if not vps_data[uid]: del vps_data[uid]
        save_vps_data()
        await update(4)
        if not vps_data.get(uid) and ctx.guild:
            role = await get_or_create_vps_role(ctx.guild)
            if role and role in user.roles:
                try: await user.remove_roles(role, reason="No VPS remaining")
                except discord.Forbidden: pass
        await update(5)
        try:
            await user.send(embed=warn_embed("VPS Deleted",
                f"Your VPS `{cn}` has been permanently deleted by an admin.\n"
                f"**Reason:** {reason}"))
        except discord.Forbidden: pass

        done_embed = build_embed(
            "🗑️  VPS Deleted",
            f"**Container:** `{cn}`  ·  **Owner:** {user.mention}\n"
            f"**Reason:** {reason}\n\n"
            f"{mini_bar(TOTAL, TOTAL)}\n\n"
            f"{done_steps(DEL_STEPS)}",
            Colors.SUCCESS
        )
        try: await msg.edit(embed=done_embed)
        except Exception: pass

        embed = success_embed("VPS Deleted",
            f"Container `{cn}` has been permanently removed.")
        field(embed, "👤  Former Owner", user.mention, True)
        field(embed, "📝  Reason",       reason,       True)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(embed=error_embed("Deletion Failed",f"```\n{e}\n```"))

# ══════════════════════════════════════════════
#  RESOURCE MANAGEMENT
# ══════════════════════════════════════════════
async def _find_vps(container:str):
    for uid, vlist in vps_data.items():
        for i, vps in enumerate(vlist):
            if vps['container_name'] == container:
                return vps, uid, i
    return None, None, None

@bot.command(name='add-resources')
@is_admin()
async def add_resources(ctx, vps_id:str, ram:int=None, cpu:int=None, disk:int=None):
    if ram is None and cpu is None and disk is None:
        await ctx.send(embed=error_embed("Missing Parameters",
            "Specify at least one: `ram`, `cpu`, or `disk`.")); return
    fvps, uid, idx = await _find_vps(vps_id)
    if not fvps:
        await ctx.send(embed=error_embed("VPS Not Found", f"No container: `{vps_id}`")); return
    was_running  = fvps.get('status')=='running' and not fvps.get('suspended')
    disk_changed = disk is not None
    if was_running:
        await ctx.send(embed=info_embed("Pausing VPS",
            f"Stopping `{vps_id}` to apply resource changes…"))
        try:
            await execute_lxc(f"lxc stop {vps_id}"); fvps['status']='stopped'; save_vps_data()
        except Exception as e:
            await ctx.send(embed=error_embed("Stop Failed", str(e))); return
    changes = []
    try:
        nr=int(fvps['ram'].replace('GB','')); nc=int(fvps['cpu']); nd=int(fvps['storage'].replace('GB',''))
        if ram and ram>0:
            nr+=ram
            await execute_lxc(f"lxc config set {vps_id} limits.memory {nr*1024}MB")
            changes.append(f"RAM  +{ram}GB → total `{nr}GB`")
        if cpu and cpu>0:
            nc+=cpu
            await execute_lxc(f"lxc config set {vps_id} limits.cpu {nc}")
            changes.append(f"CPU  +{cpu} vCPU → total `{nc}` vCPU")
        if disk and disk>0:
            nd+=disk
            await execute_lxc(f"lxc config device set {vps_id} root size={nd}GB")
            changes.append(f"Disk +{disk}GB → total `{nd}GB`")
        fvps['ram']=f"{nr}GB"; fvps['cpu']=str(nc); fvps['storage']=f"{nd}GB"
        fvps['config']=f"{nr}GB RAM / {nc} vCPU / {nd}GB Disk"
        vps_data[uid][idx]=fvps; save_vps_data()
        if was_running:
            await execute_lxc(f"lxc start {vps_id}"); fvps['status']='running'; save_vps_data()
            await apply_internal_permissions(vps_id); await recreate_port_forwards(vps_id)
        embed = success_embed("Resources Added ✅",
            f"Successfully upgraded **`{vps_id}`**.")
        field(embed, "📈  Changes", "\n".join(f"› {c}" for c in changes), False)
        if disk_changed:
            field(embed, "💾  Disk Note",
                "Run `sudo resize2fs /` inside the VPS to claim new space.", False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=error_embed("Resource Update Failed", f"```\n{e}\n```"))

@bot.command(name='resize-vps')
@is_admin()
async def resize_vps(ctx, container:str, ram:int=None, cpu:int=None, disk:int=None):
    if ram is None and cpu is None and disk is None:
        await ctx.send(embed=error_embed("Missing Parameters",
            "Specify at least one: `ram`, `cpu`, or `disk`.")); return
    fvps, uid, idx = await _find_vps(container)
    if not fvps:
        await ctx.send(embed=error_embed("Not Found", f"Container `{container}` not found.")); return
    was_running  = fvps.get('status')=='running' and not fvps.get('suspended')
    disk_changed = disk is not None
    if was_running:
        await ctx.send(embed=info_embed("Pausing VPS", f"Stopping `{container}` for resize…"))
        try:
            await execute_lxc(f"lxc stop {container}"); fvps['status']='stopped'; save_vps_data()
        except Exception as e:
            await ctx.send(embed=error_embed("Stop Failed", str(e))); return
    changes = []
    try:
        nr=int(fvps['ram'].replace('GB','')); nc=int(fvps['cpu']); nd=int(fvps['storage'].replace('GB',''))
        if ram and ram>0:
            nr=ram
            await execute_lxc(f"lxc config set {container} limits.memory {ram*1024}MB")
            changes.append(f"RAM → `{ram}GB`")
        if cpu and cpu>0:
            nc=cpu
            await execute_lxc(f"lxc config set {container} limits.cpu {cpu}")
            changes.append(f"CPU → `{cpu} vCPU`")
        if disk and disk>0:
            nd=disk
            await execute_lxc(f"lxc config device set {container} root size={disk}GB")
            changes.append(f"Disk → `{disk}GB`")
        fvps['ram']=f"{nr}GB"; fvps['cpu']=str(nc); fvps['storage']=f"{nd}GB"
        fvps['config']=f"{nr}GB RAM / {nc} vCPU / {nd}GB Disk"
        vps_data[uid][idx]=fvps; save_vps_data()
        if was_running:
            await execute_lxc(f"lxc start {container}"); fvps['status']='running'; save_vps_data()
            await apply_internal_permissions(container); await recreate_port_forwards(container)
        embed = success_embed("VPS Resized ✅", f"**`{container}`** has been resized.")
        field(embed, "⚙️  New Specs", "\n".join(f"› {c}" for c in changes), False)
        if disk_changed:
            field(embed, "💾  Disk Note",
                "Run `sudo resize2fs /` inside the VPS to apply filesystem expansion.", False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=error_embed("Resize Failed", f"```\n{e}\n```"))

# ══════════════════════════════════════════════
#  ADMIN MANAGEMENT
# ══════════════════════════════════════════════
@bot.command(name='admin-add')
@is_main_admin()
async def admin_add(ctx, user:discord.Member):
    uid = str(user.id)
    if uid==str(MAIN_ADMIN_ID):
        await ctx.send(embed=error_embed("Already Main Admin","This user is the main admin.")); return
    if uid in admin_data.get("admins",[]):
        await ctx.send(embed=error_embed("Already Admin",f"{user.mention} is already an admin.")); return

    STEPS = [
        ("Verifying user",      "verify"),
        ("Granting admin role", "grant"),
        ("Saving to database",  "save"),
        ("Notifying user",      "notify"),
    ]
    TOTAL = len(STEPS)
    def promo_embed(step_idx:int) -> discord.Embed:
        return build_embed(
            "🛡️  Granting Admin Access",
            f"**User:** {user.mention}\n**Promoted by:** {ctx.author.mention}\n\n"
            f"{mini_bar(step_idx, TOTAL)}\n\n{step_list(STEPS, step_idx)}",
            Colors.GOLD
        )

    msg = await ctx.send(embed=promo_embed(0))
    async def update(i):
        try: await msg.edit(embed=promo_embed(i))
        except Exception: pass

    await update(1)
    admin_data["admins"].append(uid)
    await update(2)
    save_admin_data()
    await update(3)
    try:
        await user.send(embed=gold_embed("🎉  Admin Promotion",
            f"You've been promoted to **Admin** on **{BOT_NAME}** by {ctx.author.mention}!\n"
            f"› Use `{PREFIX}help` to see all admin commands."))
    except discord.Forbidden: pass

    done_embed = build_embed(
        "🛡️  Granting Admin Access",
        f"**User:** {user.mention}\n**Promoted by:** {ctx.author.mention}\n\n"
        f"{mini_bar(TOTAL, TOTAL)}\n\n{done_steps(STEPS)}",
        Colors.SUCCESS
    )
    try: await msg.edit(embed=done_embed)
    except Exception: pass
    await ctx.send(embed=success_embed("Admin Added 🛡️",
        f"{user.mention} has been granted admin privileges."))

@bot.command(name='admin-remove')
@is_main_admin()
async def admin_remove(ctx, user:discord.Member):
    uid = str(user.id)
    if uid==str(MAIN_ADMIN_ID):
        await ctx.send(embed=error_embed("Cannot Remove","You cannot remove the main admin.")); return
    if uid not in admin_data.get("admins",[]):
        await ctx.send(embed=error_embed("Not Admin",f"{user.mention} is not an admin.")); return

    STEPS = [
        ("Verifying admin",      "v"),
        ("Revoking privileges",  "r"),
        ("Saving to database",   "s"),
        ("Notifying user",       "n"),
    ]
    TOTAL = len(STEPS)
    def prog_embed(i):
        return build_embed(
            "🚫  Revoking Admin Access",
            f"**User:** {user.mention}\n**Revoked by:** {ctx.author.mention}\n\n"
            f"{mini_bar(i, TOTAL)}\n\n{step_list(STEPS, i)}",
            Colors.ERROR
        )

    msg = await ctx.send(embed=prog_embed(0))
    async def update(i):
        try: await msg.edit(embed=prog_embed(i))
        except Exception: pass

    await update(1); admin_data["admins"].remove(uid)
    await update(2); save_admin_data()
    await update(3)
    try:
        await user.send(embed=warn_embed("Admin Role Revoked",
            f"Your admin access on **{BOT_NAME}** was removed by {ctx.author.mention}."))
    except discord.Forbidden: pass

    done_embed = build_embed(
        "🚫  Revoking Admin Access",
        f"**User:** {user.mention}\n**Revoked by:** {ctx.author.mention}\n\n"
        f"{mini_bar(TOTAL, TOTAL)}\n\n{done_steps(STEPS)}",
        Colors.SUCCESS
    )
    try: await msg.edit(embed=done_embed)
    except Exception: pass
    await ctx.send(embed=success_embed("Admin Removed",
        f"{user.mention}'s admin privileges have been revoked."))

@bot.command(name='admin-list')
@is_main_admin()
async def admin_list(ctx):
    main   = await bot.fetch_user(MAIN_ADMIN_ID)
    admins = admin_data.get("admins", [])
    total  = len(admins) + 1          # +1 for main admin
    MAX_ADMINS = 10                   # visual cap for the bar
    bar    = progress_bar(min(total / MAX_ADMINS * 100, 100))

    embed = gold_embed("Admin Team Directory",
        f"All administrators of **{BOT_NAME}**.\n{DIV}")

    field(embed, "👑  Main Administrator",
        f"{main.mention}\n› ID: `{MAIN_ADMIN_ID}`", False)

    if admins:
        lines = []
        for i, aid in enumerate(admins, start=1):
            try:
                au = await bot.fetch_user(int(aid))
                lines.append(f"`{i:02}.` {au.mention}  —  `{aid}`")
            except:
                lines.append(f"`{i:02}.` Unknown  —  `{aid}`")
        field(embed, "🛡️  Administrators", "\n".join(lines), False)
    else:
        field(embed, "🛡️  Administrators", "› No additional admins assigned.", False)

    field(embed, "📊  Team Size",
        f"{bar}\n› `{total}` / `{MAX_ADMINS}` admin slots used", False)
    field(embed, "👥  Total", f"`{total}`", True)
    field(embed, "🛡️  Sub-Admins", f"`{len(admins)}`", True)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════
#  USER INFO
# ══════════════════════════════════════════════
@bot.command(name='userinfo')
@is_admin()
async def user_info(ctx, user:discord.Member):
    uid   = str(user.id)
    vlist = vps_data.get(uid, [])
    is_adm = uid==str(MAIN_ADMIN_ID) or uid in admin_data.get("admins",[])
    embed  = build_embed(
        f"User Profile — {user.display_name}",
        f"Detailed record for {user.mention}\n{DIV}",
        Colors.GOLD if is_adm else Colors.PRIMARY
    )
    joined = user.joined_at.strftime('%d %b %Y') if user.joined_at else 'Unknown'
    role_label = '👑 Main Admin' if uid==str(MAIN_ADMIN_ID) else '🛡️ Admin' if is_adm else '👤 User'
    field(embed, "👤  Identity",
        f"› **Tag:** {user.name}\n"
        f"› **ID:** `{user.id}`\n"
        f"› **Joined:** {joined}\n"
        f"› **Role:** {role_label}", False)
    if vlist:
        run  = sum(1 for v in vlist if v.get('status')=='running' and not v.get('suspended'))
        sus  = sum(1 for v in vlist if v.get('suspended'))
        tr   = sum(int(v['ram'].replace('GB','')) for v in vlist)
        tc   = sum(int(v['cpu']) for v in vlist)
        td   = sum(int(v['storage'].replace('GB','')) for v in vlist)
        run_pct = run / len(vlist) * 100 if vlist else 0
        field(embed, "📊  VPS Health", f"{progress_bar(run_pct)}\n› `{run}` / `{len(vlist)}` running", False)
        field(embed, "🖥️  VPS Summary",
            f"› **Total:** `{len(vlist)}`\n"
            f"› 🟢 Running: `{run}`\n"
            f"› 🔒 Suspended: `{sus}`\n"
            f"› **Total RAM:** `{tr}GB`\n"
            f"› **Total CPU:** `{tc}` vCPU\n"
            f"› **Total Disk:** `{td}GB`", True)
        lines  = [f"{status_badge(v)} VPS #{i+1}: `{v['container_name']}`" for i,v in enumerate(vlist)]
        chunks = [lines[j:j+5] for j in range(0, len(lines), 5)]
        for ci, chunk in enumerate(chunks, 1):
            field(embed, f"📋  VPS List {'(cont.)' if ci>1 else ''}", "\n".join(chunk), True)
    else:
        field(embed, "🖥️  VPS", "No VPS assigned.", True)
    pa = get_user_allocation(uid); pu = get_user_used_ports(uid)
    port_pct = pu / pa * 100 if pa > 0 else 0
    field(embed, "🔌  Port Quota",
        f"{progress_bar(port_pct)}\n› Allocated: `{pa}` · Used: `{pu}` · Free: `{pa-pu}`", False)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════
#  SERVER STATS
# ══════════════════════════════════════════════
@bot.command(name='serverstats')
@is_admin()
async def server_stats(ctx):
    total=run=sus=wl=0; tr=tc=td=0
    for vlist in vps_data.values():
        for v in vlist:
            total+=1
            try: tr+=int(v['ram'].replace('GB','')); tc+=int(v['cpu']); td+=int(v['storage'].replace('GB',''))
            except: pass
            if v.get('status')=='running' and not v.get('suspended'): run+=1
            if v.get('suspended'):   sus+=1
            if v.get('whitelisted'): wl+=1
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT SUM(allocated_ports) FROM port_allocations')
    tpa=cur.fetchone()[0] or 0
    cur.execute('SELECT COUNT(*) FROM port_forwards')
    tpu=cur.fetchone()[0]; conn.close()
    cpu_h=get_cpu_usage(); ram_h=get_ram_usage()

    embed = info_embed("Platform Statistics 📊",
        f"Live infrastructure snapshot for **{BOT_NAME}**.\n{DIV}")
    field(embed, "👥  Community",
        f"› **Users:** `{len(vps_data)}`\n"
        f"› **Admins:** `{len(admin_data.get('admins',[]))+1}`", True)
    field(embed, "🖥️  VPS Fleet",
        f"› **Total:** `{total}`\n"
        f"› 🟢 Running: `{run}`\n"
        f"› 🔒 Suspended: `{sus}`\n"
        f"› 🛡️ Whitelisted: `{wl}`", True)
    field(embed, "💾  Allocated Resources",
        f"› **RAM:** `{tr}GB`\n"
        f"› **CPU:** `{tc}` vCPU\n"
        f"› **Storage:** `{td}GB`", True)
    field(embed, "🔌  Port Forwarding",
        f"› **Allocated:** `{tpa}`\n"
        f"› **In Use:** `{tpu}`", True)
    field(embed, "🖥️  Host CPU",  progress_bar(cpu_h), True)
    field(embed, "🧠  Host RAM",  progress_bar(ram_h), True)
    run_pct = run / total * 100 if total else 0
    sus_pct = sus / total * 100 if total else 0
    field(embed, "📊  Running Fleet",   f"{progress_bar(run_pct)}\n› `{run}` / `{total}` VPS", True)
    field(embed, "🔒  Suspended Fleet", f"{progress_bar(sus_pct)}\n› `{sus}` / `{total}` VPS", True)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════
#  VPS INFO
# ══════════════════════════════════════════════
@bot.command(name='vpsinfo')
@is_admin()
async def vps_info(ctx, container:str=None):
    if not container:
        all_vps = []
        for uid, vlist in vps_data.items():
            try: u = await bot.fetch_user(int(uid))
            except: u = None
            uname = u.name if u else f"ID:{uid}"
            for i, v in enumerate(vlist):
                all_vps.append(f"{status_badge(v)} **{uname}** — #{i+1} `{v['container_name']}`")
        chunks = [all_vps[i:i+8] for i in range(0, len(all_vps), 8)]
        for idx, chunk in enumerate(chunks, 1):
            emb = info_embed(f"VPS Registry — {idx}/{len(chunks) or 1}", "")
            field(emb, "All Containers", "\n".join(chunk) if chunk else "No VPS found.", False)
            await ctx.send(embed=emb)
        return

    fvps=None; fu=None
    for uid, vlist in vps_data.items():
        for v in vlist:
            if v['container_name']==container:
                fvps=v
                try: fu = await bot.fetch_user(int(uid))
                except: fu = None
                break
        if fvps: break
    if not fvps:
        await ctx.send(embed=error_embed("Not Found",
            f"No VPS with container name `{container}`.")); return

    embed = build_embed(f"VPS Report — `{container}`",
        f"{status_badge(fvps)}\n{DIV}", status_color(fvps))
    field(embed, "👤  Owner",
        f"{fu.mention if fu else 'Unknown'}\n› ID: `{fu.id if fu else '?'}`", True)
    field(embed, "💾  Specs",
        f"› **RAM:** `{fvps['ram']}`\n"
        f"› **CPU:** `{fvps['cpu']}` vCPU\n"
        f"› **Disk:** `{fvps['storage']}`", True)
    field(embed, "🐧  OS",
        f"`{fvps.get('os_version','ubuntu:22.04')}`", True)
    field(embed, "📅  Created",
        f"`{datetime.fromisoformat(fvps.get('created_at',datetime.now().isoformat())).strftime('%d %b %Y  %H:%M')}`", True)
    if fvps.get('shared_with'):
        shared = []
        for sid in fvps['shared_with']:
            try:
                su = await bot.fetch_user(int(sid)); shared.append(f"› {su.mention}")
            except: shared.append(f"› `{sid}`")
        field(embed, "🔗  Shared With", "\n".join(shared), True)
    conn=get_db(); cur=conn.cursor()
    cur.execute('SELECT COUNT(*) FROM port_forwards WHERE vps_container=?',(container,))
    pcnt=cur.fetchone()[0]; conn.close()
    # port usage bar (cap at 10 for visual)
    port_pct = min(pcnt / 10 * 100, 100)
    field(embed, "🔌  Port Forwards",
        f"{progress_bar(port_pct)}\n› `{pcnt}` active forward(s) — TCP & UDP", False)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════
#  OPERATIONS
# ══════════════════════════════════════════════
@bot.command(name='restart-vps')
@is_admin()
async def restart_vps(ctx, container:str):
    await ctx.send(embed=info_embed("Restarting VPS…",
        f"Restarting `{container}`, please wait…"))
    try:
        await execute_lxc(f"lxc restart {container}")
        for vlist in vps_data.values():
            for v in vlist:
                if v['container_name']==container:
                    v['status']='running'; v['suspended']=False; save_vps_data(); break
        await apply_internal_permissions(container); await recreate_port_forwards(container)
        await ctx.send(embed=success_embed("VPS Restarted 🔁",
            f"`{container}` is back online with all port forwards restored."))
    except Exception as e:
        await ctx.send(embed=error_embed("Restart Failed", f"```\n{e}\n```"))

@bot.command(name='exec')
@is_admin()
async def execute_command(ctx, container:str, *, command:str):
    await ctx.send(embed=info_embed("Executing Command",
        f"Running in `{container}`:\n```bash\n{command}\n```"))
    try:
        proc = await asyncio.create_subprocess_exec("lxc","exec",container,"--","bash","-c",command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        out = stdout.decode() if stdout else "No output"
        err = stderr.decode() if stderr else ""
        embed = build_embed(f"Command Output — `{container}`",
            f"```bash\n{command}\n```",
            Colors.SUCCESS if proc.returncode==0 else Colors.ERROR)
        if out.strip():
            o = out[:1000]+"…" if len(out)>1000 else out
            field(embed, "📤  Output", f"```\n{o}\n```", False)
        if err.strip():
            e = err[:500]+"…" if len(err)>500 else err
            field(embed, "⚠️  Stderr", f"```\n{e}\n```", False)
        field(embed, "🔢  Exit Code", f"`{proc.returncode}`", True)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=error_embed("Execution Failed", f"```\n{e}\n```"))

@bot.command(name='stop-vps-all')
@is_admin()
async def stop_all_vps(ctx):
    embed = warn_embed("Stop All VPS — Confirmation",
        f"⚠️ This will **force-stop ALL running VPS** on the server.\n\n"
        f"› This action **cannot be undone**.\n"
        f"› Only confirm if you know what you're doing.")
    class ConfirmAll(discord.ui.View):
        def __init__(self): super().__init__(timeout=60)

        @discord.ui.button(label="Stop All VPS", style=discord.ButtonStyle.danger, emoji="🛑")
        async def confirm(self, interaction:discord.Interaction, item:discord.ui.Button):
            await interaction.response.defer()
            try:
                proc = await asyncio.create_subprocess_exec("lxc","stop","--all","--force",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await proc.communicate()
                if proc.returncode==0:
                    cnt=0
                    for vlist in vps_data.values():
                        for v in vlist:
                            if v.get('status')=='running':
                                v['status']='stopped'; v['suspended']=False; cnt+=1
                    save_vps_data()
                    emb = success_embed("All VPS Stopped 🛑",
                        f"Successfully stopped **{cnt}** VPS instance(s).")
                    field(emb, "📋  Output",
                        f"```\n{stdout.decode()[:500] if stdout else 'Done'}\n```", False)
                    await interaction.followup.send(embed=emb)
                else:
                    await interaction.followup.send(embed=error_embed("Failed",
                        stderr.decode()[:500] if stderr else "Unknown error"))
            except Exception as e:
                await interaction.followup.send(embed=error_embed("Error", str(e)))

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
        async def cancel(self, interaction:discord.Interaction, item:discord.ui.Button):
            await interaction.response.edit_message(embed=info_embed("Operation Cancelled",
                "No VPS were stopped."))
    await ctx.send(embed=embed, view=ConfirmAll())

# ══════════════════════════════════════════════
#  RESOURCE MONITOR CONTROL
# ══════════════════════════════════════════════
@bot.command(name='cpu-monitor')
@is_admin()
async def monitor_control(ctx, action:str="status"):
    global resource_monitor_active
    if action.lower() == "status":
        st = "🟢 Active" if resource_monitor_active else "🔴 Inactive"
        embed = info_embed("Resource Monitor",
            f"**Status:** {st}  *(logging only, no auto-stop)*\n{DIV}")
        field(embed, "⚙️  Thresholds",
            f"› CPU: `{CPU_THRESHOLD}%`  ·  RAM: `{RAM_THRESHOLD}%`", True)
        field(embed, "⏰  Interval", "`60 seconds`", True)
        await ctx.send(embed=embed)
    elif action.lower() == "enable":
        resource_monitor_active = True
        await ctx.send(embed=success_embed("Monitor Enabled",
            "Resource monitoring is now **active** and logging every 60s."))
    elif action.lower() == "disable":
        resource_monitor_active = False
        await ctx.send(embed=warn_embed("Monitor Disabled",
            "Resource monitoring has been **disabled**."))
    else:
        await ctx.send(embed=error_embed("Invalid Action",
            f"Use: `{PREFIX}cpu-monitor <status|enable|disable>`"))

# ══════════════════════════════════════════════
#  ADVANCED OPERATIONS
# ══════════════════════════════════════════════
@bot.command(name='clone-vps')
@is_admin()
async def clone_vps(ctx, container:str, new_name:str=None):
    if not new_name:
        ts       = datetime.now().strftime('%Y%m%d-%H%M%S')
        new_name = f"{BOT_NAME.lower().replace(' ','-')}-{container}-clone-{ts}"
    await ctx.send(embed=info_embed("Cloning VPS…",
        f"Cloning `{container}` → `{new_name}`…\n*This may take a few minutes.*"))
    try:
        fvps, uid, _ = await _find_vps(container)
        if not fvps:
            await ctx.send(embed=error_embed("Not Found",
                f"Container `{container}` not found.")); return
        await execute_lxc(f"lxc copy {container} {new_name}")
        await apply_lxc_config(new_name); await execute_lxc(f"lxc start {new_name}")
        await apply_internal_permissions(new_name)
        if uid not in vps_data: vps_data[uid]=[]
        nv = fvps.copy(); nv.update({
            'container_name':new_name,'status':'running','suspended':False,
            'whitelisted':False,'suspension_history':[],'created_at':datetime.now().isoformat(),
            'shared_with':[],'id':None
        })
        vps_data[uid].append(nv); save_vps_data()
        embed = success_embed("VPS Cloned ✅",
            f"`{container}` → `{new_name}` — clone is running.")
        field(embed, "💾  Resources",
            f"› **RAM:** {nv['ram']}  ·  **CPU:** {nv['cpu']} vCPU  ·  **Disk:** {nv['storage']}", False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=error_embed("Clone Failed", f"```\n{e}\n```"))

@bot.command(name='migrate-vps')
@is_admin()
async def migrate_vps(ctx, container:str, target_pool:str):
    await ctx.send(embed=info_embed("Migrating VPS…",
        f"Moving `{container}` → pool `{target_pool}`…\n*Please wait, this may take several minutes.*"))
    try:
        await execute_lxc(f"lxc stop {container}")
        tmp = f"{BOT_NAME.lower().replace(' ','-')}-{container}-temp-{int(time.time())}"
        await execute_lxc(f"lxc copy {container} {tmp} -s {target_pool}")
        await execute_lxc(f"lxc delete {container} --force")
        await execute_lxc(f"lxc rename {tmp} {container}")
        await apply_lxc_config(container); await execute_lxc(f"lxc start {container}")
        await apply_internal_permissions(container); await recreate_port_forwards(container)
        for vlist in vps_data.values():
            for v in vlist:
                if v['container_name']==container:
                    v['status']='running'; v['suspended']=False; save_vps_data(); break
        await ctx.send(embed=success_embed("Migration Complete",
            f"`{container}` moved to pool `{target_pool}` and is running."))
    except Exception as e:
        await ctx.send(embed=error_embed("Migration Failed", f"```\n{e}\n```"))

@bot.command(name='vps-stats')
@is_admin()
async def vps_stats(ctx, container:str):
    await ctx.send(embed=info_embed("Gathering Stats…",
        f"Collecting live data for `{container}`…"))
    try:
        status  = await get_container_status(container)
        cpu_pct = await get_container_cpu_pct(container)
        ram_pct = await get_container_ram_pct(container)
        mem     = await get_container_memory(container)
        disk    = await get_container_disk(container)
        up      = await get_container_uptime(container)
        embed   = build_embed(f"VPS Statistics — `{container}`",
            f"Live resource snapshot.\n{DIV}",
            Colors.RUNNING if status=='running' else Colors.STOPPED)
        field(embed, "🖥️  Status",  f"`{status.upper()}`",            True)
        field(embed, "⏱️  Uptime",  f"```{up}```",                    True)
        field(embed, "💻  CPU",     progress_bar(cpu_pct),            True)
        field(embed, "🧠  Memory",  progress_bar(ram_pct)+f"\n`{mem}`", True)
        field(embed, "💾  Disk",    f"`{disk}`",                      True)
        fvps,_,__ = await _find_vps(container)
        if fvps:
            field(embed, "📊  Allocated",
                f"› **RAM:** `{fvps['ram']}`  ·  "
                f"**CPU:** `{fvps['cpu']}` vCPU  ·  "
                f"**Disk:** `{fvps['storage']}`", False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=error_embed("Stats Failed", f"```\n{e}\n```"))

@bot.command(name='vps-network')
@is_admin()
async def vps_network(ctx, container:str, action:str, value:str=None):
    if action.lower() not in ["list","add","remove","limit"]:
        await ctx.send(embed=error_embed("Invalid Action",
            f"Usage: `{PREFIX}vps-network <container> <list|add|remove|limit> [value]`")); return
    try:
        if action.lower()=="list":
            proc=await asyncio.create_subprocess_exec("lxc","exec",container,"--","ip","addr",
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await proc.communicate()
            if proc.returncode==0:
                out=stdout.decode(); out=out[:1000]+"…" if len(out)>1000 else out
                embed=info_embed(f"Network Interfaces — `{container}`", "")
                field(embed,"🌐  Interfaces",f"```\n{out}\n```",False)
                await ctx.send(embed=embed)
            else: await ctx.send(embed=error_embed("Error",stderr.decode()[:500]))
        elif action.lower()=="limit" and value:
            await execute_lxc(f"lxc config device set {container} eth0 limits.egress {value}")
            await execute_lxc(f"lxc config device set {container} eth0 limits.ingress {value}")
            await ctx.send(embed=success_embed("Network Limited",
                f"Bandwidth capped to `{value}` for `{container}`."))
        elif action.lower()=="add" and value:
            await execute_lxc(f"lxc config device add {container} eth1 nic nictype=bridged parent={value}")
            await ctx.send(embed=success_embed("Interface Added",
                f"Added NIC bridge `{value}` to `{container}`."))
        elif action.lower()=="remove" and value:
            await execute_lxc(f"lxc config device remove {container} {value}")
            await ctx.send(embed=success_embed("Interface Removed",
                f"Removed `{value}` from `{container}`."))
        else:
            await ctx.send(embed=error_embed("Invalid Parameters",
                "Provide valid parameters for the chosen action."))
    except Exception as e:
        await ctx.send(embed=error_embed("Network Error", f"```\n{e}\n```"))

@bot.command(name='vps-processes')
@is_admin()
async def vps_processes(ctx, container:str):
    await ctx.send(embed=info_embed("Listing Processes…",
        f"Fetching process list from `{container}`…"))
    try:
        proc=await asyncio.create_subprocess_exec("lxc","exec",container,"--","ps","aux",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,stderr=await proc.communicate()
        if proc.returncode==0:
            out=stdout.decode(); out=out[:1000]+"…" if len(out)>1000 else out
            embed=info_embed(f"Processes — `{container}`", "")
            field(embed,"⚙️  Process List",f"```\n{out}\n```",False)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=error_embed("Error",stderr.decode()[:500]))
    except Exception as e:
        await ctx.send(embed=error_embed("Failed", f"```\n{e}\n```"))

@bot.command(name='vps-logs')
@is_admin()
async def vps_logs(ctx, container:str, lines:int=50):
    await ctx.send(embed=info_embed("Fetching Logs…",
        f"Getting last `{lines}` log entries from `{container}`…"))
    try:
        proc=await asyncio.create_subprocess_exec("lxc","exec",container,"--","journalctl","-n",str(lines),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,stderr=await proc.communicate()
        if proc.returncode==0:
            out=stdout.decode(); out=out[:1000]+"…" if len(out)>1000 else out
            embed=info_embed(f"System Logs — `{container}`",f"Last `{lines}` entries")
            field(embed,"📋  Logs",f"```\n{out}\n```",False)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=error_embed("Error",stderr.decode()[:500]))
    except Exception as e:
        await ctx.send(embed=error_embed("Failed", f"```\n{e}\n```"))

@bot.command(name='vps-uptime')
@is_admin()
async def vps_uptime(ctx, container:str):
    up    = await get_container_uptime(container)
    embed = info_embed(f"Uptime — `{container}`", f"```\n{up}\n```")
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════
#  SUSPEND / UNSUSPEND
# ══════════════════════════════════════════════
@bot.command(name='suspend-vps')
@is_admin()
async def suspend_vps(ctx, container:str, *, reason:str="Administrative action"):
    found = False
    for uid, lst in vps_data.items():
        for v in lst:
            if v['container_name'] == container:
                if v.get('status') != 'running':
                    await ctx.send(embed=error_embed("Cannot Suspend",
                        "VPS must be running to suspend.")); return
                try:
                    await execute_lxc(f"lxc stop {container}")
                    v['status']='stopped'; v['suspended']=True
                    v.setdefault('suspension_history',[]).append({
                        'time': datetime.now().isoformat(),
                        'reason': reason,
                        'by': f"{ctx.author.name} ({ctx.author.id})"
                    })
                    save_vps_data()
                except Exception as e:
                    await ctx.send(embed=error_embed("Suspend Failed", str(e))); return
                try:
                    owner = await bot.fetch_user(int(uid))
                    await owner.send(embed=error_embed(f"🔒  VPS Suspended — `{container}`",
                        f"Your VPS has been suspended by an admin.\n\n"
                        f"**Reason:** {reason}\n\n"
                        f"› Contact an admin to dispute or resolve this."))
                except: pass
                await ctx.send(embed=success_embed("VPS Suspended 🔒",
                    f"`{container}` has been suspended.\n› **Reason:** {reason}"))
                found = True; break
        if found: break
    if not found:
        await ctx.send(embed=error_embed("Not Found",
            f"Container `{container}` not found."))

@bot.command(name='unsuspend-vps')
@is_admin()
async def unsuspend_vps(ctx, container:str):
    found = False
    for uid, lst in vps_data.items():
        for v in lst:
            if v['container_name'] == container:
                if not v.get('suspended'):
                    await ctx.send(embed=error_embed("Not Suspended",
                        "This VPS is not currently suspended.")); return
                try:
                    v['suspended']=False; v['status']='running'
                    await execute_lxc(f"lxc start {container}")
                    await apply_internal_permissions(container)
                    await recreate_port_forwards(container); save_vps_data()
                    await ctx.send(embed=success_embed("VPS Unsuspended 🟢",
                        f"`{container}` has been unsuspended and is now running."))
                    found = True
                except Exception as e:
                    await ctx.send(embed=error_embed("Start Failed", str(e)))
                try:
                    owner = await bot.fetch_user(int(uid))
                    await owner.send(embed=success_embed("VPS Unsuspended 🟢",
                        f"Your VPS `{container}` has been unsuspended by an admin.\n"
                        f"› You can manage it again now."))
                except: pass
                break
        if found: break
    if not found:
        await ctx.send(embed=error_embed("Not Found",
            f"Container `{container}` not found."))

@bot.command(name='suspension-logs')
@is_admin()
async def suspension_logs(ctx, container:str=None):
    if container:
        fvps=None
        for lst in vps_data.values():
            for v in lst:
                if v['container_name']==container: fvps=v; break
            if fvps: break
        if not fvps:
            await ctx.send(embed=error_embed("Not Found",f"`{container}` not found.")); return
        hist = fvps.get('suspension_history',[])
        if not hist:
            await ctx.send(embed=info_embed("No History",
                f"No suspensions recorded for `{container}`.")); return
        embed = warn_embed(f"Suspension History — `{container}`", "")
        lines = [
            f"**{datetime.fromisoformat(h['time']).strftime('%d %b %Y  %H:%M')}**\n"
            f"› {h['reason']}\n"
            f"› By: {h['by']}"
            for h in sorted(hist, key=lambda x:x['time'], reverse=True)[:8]
        ]
        field(embed, "📋  Events", "\n\n".join(lines), False)
        await ctx.send(embed=embed)
    else:
        all_logs=[]
        for uid, lst in vps_data.items():
            for v in lst:
                for e in sorted(v.get('suspension_history',[]), key=lambda x:x['time'], reverse=True):
                    t = datetime.fromisoformat(e['time']).strftime('%d %b %y  %H:%M')
                    all_logs.append(
                        f"**{t}** — `{v['container_name']}` (<@{uid}>)\n"
                        f"› {e['reason']}  —  By: {e['by']}")
        if not all_logs:
            await ctx.send(embed=info_embed("No Logs",
                "No suspension events have been recorded.")); return
        chunks = [all_logs[i:i+6] for i in range(0, len(all_logs), 6)]
        for idx, chunk in enumerate(chunks, 1):
            emb = warn_embed(f"Global Suspension Log — {idx}/{len(chunks)}", "")
            field(emb, "📋  Events", "\n\n".join(chunk), False)
            await ctx.send(embed=emb)

@bot.command(name='apply-permissions')
@is_admin()
async def apply_permissions(ctx, container:str):
    await ctx.send(embed=info_embed("Applying Permissions…",
        f"Configuring Docker-ready permissions for `{container}`…"))
    try:
        status = await get_container_status(container)
        if status=='running': await execute_lxc(f"lxc stop {container}")
        await apply_lxc_config(container); await execute_lxc(f"lxc start {container}")
        await apply_internal_permissions(container); await recreate_port_forwards(container)
        for vlist in vps_data.values():
            for v in vlist:
                if v['container_name']==container:
                    v['status']='running'; v['suspended']=False; save_vps_data(); break
        await ctx.send(embed=success_embed("Permissions Applied ✅",
            f"`{container}` is now Docker-ready with full permissions and ports from 0."))
    except Exception as e:
        await ctx.send(embed=error_embed("Failed", f"```\n{e}\n```"))

@bot.command(name='resource-check')
@is_admin()
async def resource_check(ctx):
    embed = info_embed("Resource Check Running…",
        "Scanning all running VPS for high resource usage…")
    msg = await ctx.send(embed=embed); cnt=0
    for uid, vlist in vps_data.items():
        for v in vlist:
            if v.get('status')=='running' and not v.get('suspended') and not v.get('whitelisted'):
                cn  = v['container_name']
                cpu = await get_container_cpu_pct(cn)
                ram = await get_container_ram_pct(cn)
                if cpu>CPU_THRESHOLD or ram>RAM_THRESHOLD:
                    reason = f"High usage: CPU {cpu:.1f}% / RAM {ram:.1f}% (limits: {CPU_THRESHOLD}% / {RAM_THRESHOLD}%)"
                    logger.warning(f"Auto-suspending {cn}: {reason}")
                    try:
                        await execute_lxc(f"lxc stop {cn}")
                        v['status']='stopped'; v['suspended']=True
                        v.setdefault('suspension_history',[]).append({
                            'time':datetime.now().isoformat(),
                            'reason':reason,'by':'Resource Check'})
                        save_vps_data()
                        try:
                            o = await bot.fetch_user(int(uid))
                            await o.send(embed=error_embed("VPS Auto-Suspended",
                                f"Your VPS `{cn}` was suspended due to excessive resource usage.\n\n"
                                f"**Details:** {reason}\n\n"
                                f"› Contact an admin to unsuspend."))
                        except: pass
                        cnt+=1
                    except Exception as e:
                        logger.error(f"Failed to suspend {cn}: {e}")
    final = success_embed("Resource Check Complete",
        f"Scan finished. **{cnt}** VPS suspended due to high resource usage." if cnt else
        "Scan finished. ✅ All VPS are within acceptable resource limits.")
    await msg.edit(embed=final)

@bot.command(name='whitelist-vps')
@is_admin()
async def whitelist_vps(ctx, container:str, action:str):
    if action.lower() not in ['add','remove']:
        await ctx.send(embed=error_embed("Invalid Action",
            f"Use `{PREFIX}whitelist-vps <container> <add|remove>`")); return
    found=False
    for vlist in vps_data.values():
        for v in vlist:
            if v['container_name']==container:
                if action.lower()=='add':
                    v['whitelisted']=True
                    msg="added to the resource suspension whitelist."
                else:
                    v['whitelisted']=False
                    msg="removed from the whitelist."
                save_vps_data()
                await ctx.send(embed=success_embed("Whitelist Updated",
                    f"`{container}` has been **{msg}**"))
                found=True; break
        if found: break
    if not found:
        await ctx.send(embed=error_embed("Not Found",f"`{container}` not found."))

# ══════════════════════════════════════════════
#  SNAPSHOTS
# ══════════════════════════════════════════════
@bot.command(name='snapshot')
@is_admin()
async def snapshot_vps(ctx, container:str, snap_name:str="snap0"):
    await ctx.send(embed=info_embed("Creating Snapshot…",
        f"Snapshotting `{container}` as `{snap_name}`…"))
    try:
        await execute_lxc(f"lxc snapshot {container} {snap_name}")
        await ctx.send(embed=success_embed("Snapshot Created 📷",
            f"Snapshot `{snap_name}` successfully created for `{container}`."))
    except Exception as e:
        await ctx.send(embed=error_embed("Snapshot Failed", f"```\n{e}\n```"))

@bot.command(name='list-snapshots')
@is_admin()
async def list_snapshots(ctx, container:str):
    try:
        result = await execute_lxc(f"lxc snapshot list {container}")
        embed  = info_embed(f"Snapshots — `{container}`",
            f"```\n{result}\n```")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=error_embed("Error", f"```\n{e}\n```"))

@bot.command(name='restore-snapshot')
@is_admin()
async def restore_snapshot(ctx, container:str, snap_name:str):
    embed = warn_embed("Restore Snapshot — Confirmation",
        f"Restoring `{snap_name}` on `{container}` will **overwrite the current state**.\n\n"
        f"› This action **cannot be undone**.")
    class RestoreView(discord.ui.View):
        def __init__(self): super().__init__(timeout=60)

        @discord.ui.button(label="Confirm Restore", style=discord.ButtonStyle.danger, emoji="🔄")
        async def confirm(self, inter:discord.Interaction, item:discord.ui.Button):
            await inter.response.defer()
            try:
                await execute_lxc(f"lxc stop {container}")
                await execute_lxc(f"lxc restore {container} {snap_name}")
                await execute_lxc(f"lxc start {container}")
                await apply_internal_permissions(container); await recreate_port_forwards(container)
                for vlist in vps_data.values():
                    for v in vlist:
                        if v['container_name']==container:
                            v['status']='running'; v['suspended']=False; save_vps_data(); break
                await inter.followup.send(embed=success_embed("Snapshot Restored ✅",
                    f"`{snap_name}` successfully restored on `{container}`."))
            except Exception as e:
                await inter.followup.send(embed=error_embed("Restore Failed",
                    f"```\n{e}\n```"))

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
        async def cancel(self, inter:discord.Interaction, item:discord.ui.Button):
            await inter.response.edit_message(embed=info_embed("Restore Cancelled",
                "No changes were made."))
    await ctx.send(embed=embed, view=RestoreView())

@bot.command(name='backup-db')
@is_admin()
async def backup_db(ctx):
    bn = f"vps_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    try:
        shutil.copy('vps.db', bn)
        if os.path.exists('vps.db-wal'): shutil.copy('vps.db-wal',f"{bn}-wal")
        if os.path.exists('vps.db-shm'): shutil.copy('vps.db-shm',f"{bn}-shm")
        await ctx.send(embed=success_embed("Database Backup Created 💾",
            f"Backup saved as `{bn}`\n› WAL/SHM files included if present."))
    except Exception as e:
        await ctx.send(embed=error_embed("Backup Failed", f"```\n{e}\n```"))

@bot.command(name='repair-ports')
@is_admin()
async def repair_ports(ctx, container:str):
    await ctx.send(embed=info_embed("Repairing Port Forwards…",
        f"Re-attaching all port forwards for `{container}`…"))
    try:
        readded = await recreate_port_forwards(container)
        await ctx.send(embed=success_embed("Ports Repaired 🔌",
            f"Successfully re-added **{readded}** port forward(s) for `{container}`."))
    except Exception as e:
        await ctx.send(embed=error_embed("Repair Failed", f"```\n{e}\n```"))

# ══════════════════════════════════════════════
#  ⚡ ULTRA-PREMIUM HELP MENU
# ══════════════════════════════════════════════
class HelpView(discord.ui.View):
    CATEGORIES = {
        "home": {
            "name": "🏠  Home",
            "color": Colors.PRIMARY,
            "emoji": "🏠",
            "commands": []
        },
        "user": {
            "name": "👤  User Commands",
            "color": Colors.INFO,
            "emoji": "👤",
            "commands": [
                ("ping",          "Check bot latency and health"),
                ("uptime",        "View host system uptime"),
                ("myvps",         "List all your VPS instances"),
                ("manage",        "Interactive VPS control panel"),
                ("share-user",    "`@user <vps_num>` — Share VPS access"),
                ("share-ruser",   "`@user <vps_num>` — Revoke shared access"),
                ("manage-shared", "`@owner <vps_num>` — Control a shared VPS"),
            ]
        },
        "ports": {
            "name": "🔌  Port Forwarding",
            "color": Colors.TEAL,
            "emoji": "🔌",
            "commands": [
                ("ports",         "View your quota and port forwards"),
                ("ports add",     "`<vps_num> <port>` — Forward a port (TCP+UDP)"),
                ("ports list",    "List all active forwards"),
                ("ports remove",  "`<id>` — Delete a port forward"),
            ]
        },
        "system": {
            "name": "⚙️  System",
            "color": Colors.WARNING,
            "emoji": "⚙️",
            "commands": [
                ("serverstats",   "Full platform overview"),
                ("thresholds",    "View resource alert thresholds"),
                ("cpu-monitor",   "`<status|enable|disable>` — Control monitoring"),
                ("set-threshold", "`<cpu> <ram>` — Update alert thresholds"),
                ("set-status",    "`<type> <name>` — Change bot presence"),
            ]
        },
        "admin": {
            "name": "🛡️  Admin Panel",
            "color": Colors.PINK,
            "emoji": "🛡️",
            "admin_only": True,
            "commands": [
                ("create",            "`<ram> <cpu> <disk> @user` — Provision VPS"),
                ("delete-vps",        "`@user <vps_num> [reason]` — Delete VPS"),
                ("manage",            "`@user` — Admin-manage any user's VPS"),
                ("list-all",          "Full platform VPS registry"),
                ("lxc-list",          "Raw LXC container list"),
                ("add-resources",     "`<container> [ram] [cpu] [disk]` — Add specs"),
                ("resize-vps",        "`<container> [ram] [cpu] [disk]` — Resize specs"),
                ("restart-vps",       "`<container>` — Restart VPS"),
                ("clone-vps",         "`<container> [name]` — Clone VPS"),
                ("migrate-vps",       "`<container> <pool>` — Migrate storage"),
                ("exec",              "`<container> <cmd>` — Execute shell command"),
                ("stop-vps-all",      "Emergency stop all VPS"),
                ("suspend-vps",       "`<container> [reason]` — Suspend VPS"),
                ("unsuspend-vps",     "`<container>` — Lift suspension"),
                ("whitelist-vps",     "`<container> <add|remove>` — Whitelist"),
                ("resource-check",    "Scan and suspend high-usage VPS"),
                ("apply-permissions", "Re-apply Docker permissions"),
                ("vpsinfo",           "`[container]` — Detailed VPS report"),
                ("vps-stats",         "`<container>` — Live container stats"),
                ("vps-network",       "`<container> <action>` — Network management"),
                ("vps-processes",     "`<container>` — Process list"),
                ("vps-logs",          "`<container> [lines]` — View system logs"),
                ("vps-uptime",        "`<container>` — Container uptime"),
                ("userinfo",          "`@user` — User profile and VPS details"),
                ("snapshot",          "`<container> [name]` — Create snapshot"),
                ("list-snapshots",    "`<container>` — List snapshots"),
                ("restore-snapshot",  "`<container> <snap>` — Restore snapshot"),
                ("backup-db",         "Create database backup"),
                ("repair-ports",      "`<container>` — Repair port forwards"),
                ("suspension-logs",   "`[container]` — View suspension history"),
                ("ports-add-user",    "`<amount> @user` — Grant port slots"),
                ("ports-remove-user", "`<amount> @user` — Reduce port slots"),
                ("ports-revoke",      "`<id>` — Force-remove a port forward"),
            ]
        },
        "main_admin": {
            "name": "👑  Main Admin",
            "color": Colors.GOLD,
            "emoji": "👑",
            "main_admin_only": True,
            "commands": [
                ("admin-add",    "`@user` — Grant admin privileges"),
                ("admin-remove", "`@user` — Revoke admin privileges"),
                ("admin-list",   "View all administrators"),
            ]
        }
    }

    def __init__(self, ctx):
        super().__init__(timeout=300)
        self.ctx  = ctx; self.current = "home"
        uid = str(ctx.author.id)
        self.is_admin_user      = uid==str(MAIN_ADMIN_ID) or uid in admin_data.get("admins",[])
        self.is_main_admin_user = uid==str(MAIN_ADMIN_ID)
        self._build_select()
        self.embed = self._make_home_embed()

    def _build_select(self):
        opts = []
        for k, v in self.CATEGORIES.items():
            if v.get("main_admin_only") and not self.is_main_admin_user: continue
            if v.get("admin_only") and not self.is_admin_user: continue
            opts.append(discord.SelectOption(
                label=v["name"], value=k, emoji=v.get("emoji")
            ))
        self.select = discord.ui.Select(
            placeholder="📚  Browse Help Categories…",
            options=opts
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    def _make_home_embed(self):
        uid    = str(self.ctx.author.id)
        is_adm = uid==str(MAIN_ADMIN_ID) or uid in admin_data.get("admins",[])
        role   = "👑 Main Admin" if uid==str(MAIN_ADMIN_ID) else "🛡️ Admin" if is_adm else "👤 User"
        embed  = build_embed(
            f"⚡  {BOT_NAME} — Command Center",
            f"The authoritative VPS management platform.\n{DIV}\n"
            f"**Your Role:** {role}  ·  **Prefix:** `{PREFIX}`\n\n"
            f"Use the dropdown below to navigate command categories.",
            Colors.PRIMARY
        )
        cats = (
            "👤  **User** — Personal VPS management\n"
            "🔌  **Ports** — Port forwarding controls\n"
            "⚙️  **System** — Platform statistics & monitoring"
        )
        if self.is_admin_user:
            cats += "\n🛡️  **Admin** — Advanced administrator panel"
        if self.is_main_admin_user:
            cats += "\n👑  **Main Admin** — Team and access management"
        field(embed, "📚  Categories", cats, False)
        field(embed, "⚡  Quick Start",
            f"`{PREFIX}myvps` — View your VPS fleet\n"
            f"`{PREFIX}manage` — Open VPS control panel\n"
            f"`{PREFIX}ports list` — View port forwards\n"
            f"`{PREFIX}ping` — Check bot health", False)
        field(embed, "🔗  Support",
            "Contact an admin for VPS provisioning, upgrades, or issues.", False)
        return embed

    async def _on_select(self, interaction:discord.Interaction):
        if str(interaction.user.id) != str(self.ctx.author.id):
            await interaction.response.send_message(
                embed=error_embed("Access Denied",
                    "Only the command author can navigate this help menu."),
                ephemeral=True); return
        cat  = self.select.values[0]; data = self.CATEGORIES[cat]
        if data.get("main_admin_only") and not self.is_main_admin_user:
            await interaction.response.send_message(
                embed=error_embed("Access Denied","Main Admin only."), ephemeral=True); return
        if data.get("admin_only") and not self.is_admin_user:
            await interaction.response.send_message(
                embed=error_embed("Access Denied","Admin only."), ephemeral=True); return
        self.current = cat
        self.embed   = self._make_home_embed() if cat=="home" else self._make_category_embed(cat, data)
        await interaction.response.defer()
        await interaction.edit_original_response(embed=self.embed, view=self)

    def _make_category_embed(self, cat:str, data:dict) -> discord.Embed:
        embed = build_embed(data["name"], f"All commands in this category.\n{DIV}", data["color"])
        cmds  = data["commands"]
        lines = [f"`{PREFIX}{cmd}` — {desc}" for cmd, desc in cmds]
        chunks = [lines[i:i+8] for i in range(0, len(lines), 8)]
        for idx, chunk in enumerate(chunks, 1):
            fname = f"Commands {'(continued)' if idx>1 else ''}"
            field(embed, fname, "\n".join(chunk), False)
        if data.get("admin_only"):
            field(embed, "🔒  Access Level", "Requires **Admin** role", True)
        if data.get("main_admin_only"):
            field(embed, "👑  Access Level", "Requires **Main Admin**", True)
        return embed

@bot.command(name='help')
async def show_help(ctx):
    """⚡ Ultra-Premium interactive help menu"""
    view = HelpView(ctx)
    await ctx.send(embed=view.embed, view=view)

# ══════════════════════════════════════════════
#  ALIASES
# ══════════════════════════════════════════════
@bot.command(name='commands')
async def commands_alias(ctx):
    await show_help(ctx)

@bot.command(name='stats')
async def stats_alias(ctx):
    uid = str(ctx.author.id)
    if uid==str(MAIN_ADMIN_ID) or uid in admin_data.get("admins",[]):
        await server_stats(ctx)
    else:
        await ctx.send(embed=error_embed("Access Denied","Admin permission required."))

@bot.command(name='info')
async def info_alias(ctx, user:discord.Member=None):
    uid = str(ctx.author.id)
    if uid==str(MAIN_ADMIN_ID) or uid in admin_data.get("admins",[]):
        if user: await user_info(ctx, user)
        else: await ctx.send(embed=error_embed("Usage",f"`{PREFIX}info @user`"))
    else:
        await ctx.send(embed=error_embed("Access Denied","Admin permission required."))

@bot.command(name='mangage')
async def typo_alias(ctx):
    await ctx.send(embed=info_embed("Did you mean…",
        f"Run `{PREFIX}manage` to open your VPS control panel."))

# ══════════════════════════════════════════════
#  ENTRYPOINT
# ══════════════════════════════════════════════
if __name__ == "__main__":
    if DISCORD_TOKEN and DISCORD_TOKEN != 'YOUR_BOT_TOKEN':
        bot.run(DISCORD_TOKEN)
    else:
        logger.error("No Discord token set in DISCORD_TOKEN environment variable.")