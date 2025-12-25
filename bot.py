import random
import logging
import subprocess
import sys
import os
import re
import time
import discord
from discord.ext import commands, tasks
import docker
import asyncio
from discord import app_commands
import sqlite3
from dotenv import load_dotenv
from datetime import datetime, timezone

# Load environment variables
load_dotenv()

# Configuration from .env
TOKEN = os.getenv('TOKEN', 'DISCORD_BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # Admin user ID for checks
BOT_STATUS_NAME = os.getenv('BOT_STATUS_NAME', 'UnixNodes')
WATERMARK = os.getenv('WATERMARK', 'Powered by UnixNodes VPS Bot')
# VPS Defaults from .env
DEFAULT_RAM = os.getenv('DEFAULT_RAM', '2g')  # e.g., '2g', '4G'
DEFAULT_CPU = os.getenv('DEFAULT_CPU', '1')  # Lowered default to '1' to avoid common errors
DEFAULT_DISK = os.getenv('DEFAULT_DISK', '10G')  # e.g., '20G' - Note: Disk limit not enforced in container
VPS_HOSTNAME = os.getenv('VPS_HOSTNAME', 'unix-free')  # Base hostname, append user ID
SERVER_LIMIT = int(os.getenv('SERVER_LIMIT', 1))
TOTAL_SERVER_LIMIT = int(os.getenv('TOTAL_SERVER_LIMIT', 50))  # Global total running server limit
DATABASE_FILE = os.getenv('DATABASE_FILE', 'vps_bot.db')

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('vps_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)
client = docker.from_env()

def is_admin(member):
    if not isinstance(member, discord.Member):
        logger.warning("is_admin called with non-Member object")
        return False
    # Check user ID for admin access
    return member.id == ADMIN_ID

# Database setup with SQLite3
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    default_ram = DEFAULT_RAM
    default_cpu = DEFAULT_CPU
    default_disk = DEFAULT_DISK
    sql = f'''
        CREATE TABLE IF NOT EXISTS vps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            container_id TEXT UNIQUE NOT NULL,
            container_name TEXT NOT NULL,
            os_type TEXT NOT NULL,
            hostname TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            ssh_command TEXT,
            ram TEXT DEFAULT '{default_ram}',
            cpu TEXT DEFAULT '{default_cpu}',
            disk TEXT DEFAULT '{default_disk}',
            suspended INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    '''
    cursor.execute(sql)
    cursor.execute("PRAGMA table_info(vps)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'suspended' not in columns:
        cursor.execute("ALTER TABLE vps ADD COLUMN suspended INTEGER DEFAULT 0")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def add_user(user_id, username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def add_ban(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO bans (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

def remove_ban(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bans WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def is_banned(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM bans WHERE user_id = ?', (user_id,))
    banned = cursor.fetchone() is not None
    conn.close()
    return banned

def add_vps(user_id, container_id, container_name, os_type, hostname, ssh_command, ram=DEFAULT_RAM, cpu=DEFAULT_CPU, disk=DEFAULT_DISK):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO vps (user_id, container_id, container_name, os_type, hostname, status, ssh_command, ram, cpu, disk, suspended)
        VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, 0)
    ''', (user_id, container_id, container_name, os_type, hostname, ssh_command, ram, cpu, disk))
    conn.commit()
    conn.close()

def get_user_vps(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM vps WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    vps_list = cursor.fetchall()
    conn.close()
    return vps_list

def count_user_vps(user_id):
    return len(get_user_vps(user_id))

def get_vps_by_container_id(container_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM vps WHERE container_id = ?', (container_id,))
    vps = cursor.fetchone()
    conn.close()
    return vps

def get_vps_by_identifier(user_id, identifier):
    vps_list = get_user_vps(user_id)
    if not identifier:
        return vps_list[0] if vps_list else None
    identifier_lower = identifier.lower()
    for vps in vps_list:
        if (identifier_lower in vps['container_id'].lower() or
            identifier_lower in vps['container_name'].lower()):
            return vps
    return None

def update_vps_status(container_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE vps SET status = ? WHERE container_id = ?', (status, container_id))
    conn.commit()
    conn.close()

def update_vps_ssh(container_id, ssh_command):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE vps SET ssh_command = ? WHERE container_id = ?', (ssh_command, container_id))
    conn.commit()
    conn.close()

def update_vps_suspended(container_id, suspended):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE vps SET suspended = ? WHERE container_id = ?', (suspended, container_id))
    conn.commit()
    conn.close()

def delete_vps(container_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM vps WHERE container_id = ?', (container_id,))
    conn.commit()
    conn.close()

def get_total_instances():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM vps WHERE status = "running"')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def parse_gb(resource_str):
    match = re.match(r'(\d+(?:\.\d+)?)([mMgG])?', resource_str.lower())
    if match:
        num = float(match.group(1))
        unit = match.group(2) or 'g'
        if unit in ['g', '']:
            return num
        elif unit in ['m']:
            return num / 1024.0
    return 0.0

def get_uptime(container_id):
    try:
        output = subprocess.check_output(["docker", "inspect", "-f", "{{.State.StartedAt}}", container_id], stderr=subprocess.STDOUT).decode().strip()
        if output == "<no value>":
            return "Not running"
        start_time = datetime.fromisoformat(output.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        uptime = now - start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{days}d {hours}h {minutes}m"
    except Exception as e:
        logger.error(f"Uptime error for {container_id}: {e}")
        return "Unknown"

def get_stats(container_id):
    try:
        output = subprocess.check_output([
            "docker", "stats", "--no-stream", "--format",
            "{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}",
            container_id
        ], stderr=subprocess.STDOUT).decode().strip()
        parts = output.split('\t')
        if len(parts) == 3:
            cpu, mem, net = parts
            return {'cpu': cpu, 'mem': mem, 'net': net}
    except Exception as e:
        logger.error(f"Stats error for {container_id}: {e}")
    return {'cpu': 'N/A', 'mem': 'N/A', 'net': 'N/A'}

def get_logs(container_id, lines=50):
    try:
        output = subprocess.check_output(["docker", "logs", "--tail", str(lines), container_id], stderr=subprocess.STDOUT).decode()
        return output[-2000:]  # Truncate for Discord limit
    except Exception as e:
        logger.error(f"Logs error for {container_id}: {e}")
        return "Failed to fetch logs"

# Async Docker helpers
async def async_docker_run(image, hostname, ram, cpu, disk, container_name):
    cmd = [
        "docker", "run", "-d",
        "--privileged", "--cap-add=ALL",
        "--restart", "unless-stopped",
        f"--memory={ram}",
        f"--cpus={cpu}",
        f"--hostname={hostname}",
        f"--name={container_name}",
        image,
        "tail", "-f", "/dev/null"
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        if proc.returncode != 0:
            logger.error(f"Docker run failed: {stderr.decode()}")
            return None
        return stdout.decode().strip()
    except asyncio.TimeoutError:
        logger.error("Docker run timed out")
        return None
    except Exception as e:
        logger.error(f"Docker run error: {e}")
        return None

async def async_docker_start(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "start", container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        logger.warning(f"Docker start timeout for {container_id}")
        return False
    except Exception as e:
        logger.error(f"Docker start error for {container_id}: {e}")
        return False

async def async_docker_stop(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "stop", container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        logger.warning(f"Docker stop timeout for {container_id}")
        try:
            await asyncio.create_subprocess_exec("docker", "kill", container_id, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL).communicate()
        except:
            pass
        return False
    except Exception as e:
        logger.error(f"Docker stop error for {container_id}: {e}")
        return False

async def async_docker_restart(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "restart", container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        logger.warning(f"Docker restart timeout for {container_id}")
        return False
    except Exception as e:
        logger.error(f"Docker restart error for {container_id}: {e}")
        return False

async def async_docker_rm(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"Docker rm error for {container_id}: {e}")
        return False

async def async_install_tmate(container_id, os_type):
    install_cmd = "apt-get update && apt-get install -y tmate curl wget sudo openssh-client"
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "bash", "-c", install_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        if proc.returncode != 0:
            logger.warning(f"Tmate install warning for {container_id}: {stderr.decode()}")
        else:
            logger.info(f"Tmate installed in {container_id}")
    except asyncio.TimeoutError:
        logger.error(f"Tmate install timeout for {container_id}")
    except Exception as e:
        logger.error(f"Failed to install tmate in {container_id}: {e}")

# SSH capture
async def capture_ssh_session_line(process):
    while True:
        try:
            output = await asyncio.wait_for(process.stdout.readline(), timeout=30.0)
            if not output:
                break
            output = output.decode('utf-8').strip()
            if "ssh session:" in output.lower():
                return output.split("ssh session:")[-1].strip()
        except asyncio.TimeoutError:
            break
    return None

async def docker_exec_tmate(container_id):
    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        return exec_cmd
    except Exception as e:
        logger.error(f"Tmate exec failed: {e}")
        return None

# Generic regen SSH
async def regen_ssh_command(interaction: discord.Interaction, vps_identifier, send_response=True, target_user=None):
    if target_user is None:
        target_user = interaction.user
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="No active VPS found.", color=discord.Color.red())
        if send_response:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    if vps['status'] != "running":
        embed = discord.Embed(description="VPS must be running to generate SSH.", color=discord.Color.red())
        if send_response:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    if send_response:
        await interaction.response.defer(ephemeral=True)
    container_id = vps['container_id']
    exec_process = await docker_exec_tmate(container_id)
    if exec_process:
        ssh_line = await capture_ssh_session_line(exec_process)
        if ssh_line:
            update_vps_ssh(container_id, ssh_line)
            embed = discord.Embed(title="New SSH Session Generated", description=f"```{ssh_line}```", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
            embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
            try:
                await target_user.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Cannot DM user {target_user.id}")
                if send_response:
                    embed_dm_fail = discord.Embed(description="New SSH session generated but could not send to DMs (privacy settings).", color=discord.Color.orange())
                    await interaction.followup.send(embed=embed_dm_fail, ephemeral=True)
                else:
                    return True
            if send_response:
                embed_success = discord.Embed(description="New SSH session sent to your DMs.", color=discord.Color.green())
                await interaction.followup.send(embed=embed_success, ephemeral=True)
            return True
        else:
            embed = discord.Embed(description="Failed to generate SSH session.", color=discord.Color.red())
            if send_response:
                await interaction.followup.send(embed=embed, ephemeral=True)
            return False
    else:
        embed = discord.Embed(description="Failed to execute tmate.", color=discord.Color.red())
        if send_response:
            await interaction.followup.send(embed=embed, ephemeral=True)
        return False

# Start/Stop/Restart helpers
async def manage_vps(interaction: discord.Interaction, vps_identifier, action, target_user=None):
    if target_user is None:
        target_user = interaction.user
    await interaction.response.defer(ephemeral=True)
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="No VPS found.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    if action == "start" and vps['suspended'] and target_user == interaction.user:
        embed = discord.Embed(description="This VPS is suspended by an admin. Contact support.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    container_id = vps['container_id']
    os_type = vps['os_type']
    success = False
    if action == "start":
        success = await async_docker_start(container_id)
        if success:
            update_vps_status(container_id, "running")
    elif action == "stop":
        success = await async_docker_stop(container_id)
        if success:
            update_vps_status(container_id, "stopped")
    elif action == "restart":
        success = await async_docker_restart(container_id)
        if success:
            update_vps_status(container_id, "running")
    if success:
        os_name = "Ubuntu 22.04" if os_type == "ubuntu" else "Debian 12"
        embed = discord.Embed(title=f"VPS {action.title()}ed Successfully", description=f"OS: {os_name}", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
        if action in ["start", "restart"]:
            regen_success = await regen_ssh_command(interaction, vps_identifier, send_response=False, target_user=target_user)
            if regen_success:
                embed.description += "\nNew SSH session sent to DMs."
            else:
                embed.description += "\nFailed to generate new SSH session."
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(description=f"Failed to {action} the VPS.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

# Reinstall helper
async def reinstall_vps(interaction: discord.Interaction, vps_identifier, os_type, target_user=None):
    if target_user is None:
        target_user = interaction.user
    await interaction.response.defer(ephemeral=True)
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="No VPS found.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    container_id = vps['container_id']
    user_id = vps['user_id']
    hostname = vps['hostname']
    ram, cpu, disk = vps['ram'], vps['cpu'], vps['disk']
    # Stop and remove
    await async_docker_stop(container_id)
    await asyncio.sleep(2)
    await async_docker_rm(container_id)
    delete_vps(container_id)
    # Create new with unique name
    suffix = random.randint(1000, 9999)
    new_container_name = f"{os_type}-vps-{user_id}-{suffix}"
    image = "ubuntu:22.04" if os_type == "ubuntu" else "debian:bookworm"
    new_container_id = await async_docker_run(image, hostname, ram, cpu, disk, new_container_name)
    if new_container_id:
        await async_install_tmate(new_container_id, os_type)
        await asyncio.sleep(10)  # Wait longer for install
        exec_process = await docker_exec_tmate(new_container_id)
        ssh_line = await capture_ssh_session_line(exec_process)
        if ssh_line:
            add_vps(user_id, new_container_id, new_container_name, os_type, hostname, ssh_line, ram, cpu, disk)
            os_name = "Ubuntu 22.04" if os_type == "ubuntu" else "Debian 12"
            embed = discord.Embed(title="VPS Reinstalled Successfully", description=f"OS: {os_name}\n```{ssh_line}```", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
            embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
            try:
                await target_user.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Cannot DM user {target_user.id} for reinstall")
            embed_success = discord.Embed(description="VPS has been reinstalled. Check your DMs for details.", color=discord.Color.green())
            await interaction.followup.send(embed=embed_success, ephemeral=True)
        else:
            embed = discord.Embed(description="Reinstall failed: Unable to generate SSH.", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            await async_docker_rm(new_container_id)
    else:
        embed = discord.Embed(description="Reinstall failed: Docker creation error.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)

# Create VPS helper
async def create_vps(interaction: discord.Interaction, os_type, ram=DEFAULT_RAM, cpu=DEFAULT_CPU, disk=DEFAULT_DISK, target_user=None):
    if target_user is None:
        target_user = interaction.user
    user_id = target_user.id
    username = str(target_user)
    add_user(user_id, username)
    if is_banned(user_id):
        embed = discord.Embed(description="You are banned from creating VPS instances.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if count_user_vps(user_id) >= SERVER_LIMIT:
        embed = discord.Embed(description=f"You have reached the limit of {SERVER_LIMIT} VPS instances.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if get_total_instances() >= TOTAL_SERVER_LIMIT:
        embed = discord.Embed(description=f"Global server limit reached: {TOTAL_SERVER_LIMIT} total running instances.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    # Validate resources against host
    try:
        host_info = client.info()
        host_cpus = host_info['NCPU']
        host_mem_gb = host_info['MemTotal'] / (1024 ** 3)
        req_cpu = float(cpu)
        req_ram = parse_gb(ram)
        if req_cpu > host_cpus:
            embed = discord.Embed(description=f"Requested CPU ({req_cpu}) exceeds host limit ({host_cpus}).", color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if req_ram > host_mem_gb:
            embed = discord.Embed(description=f"Requested RAM ({req_ram}GB) exceeds host limit ({host_mem_gb:.1f}GB).", color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    except Exception as e:
        logger.error(f"Resource validation failed: {e}")
        embed = discord.Embed(description="Resource validation failed. Please contact an admin.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send("Creating your VPS instance...", ephemeral=True)
    hostname = f"{VPS_HOSTNAME}-{user_id}"
    suffix = random.randint(1000, 9999)
    container_name = f"{os_type}-vps-{user_id}-{suffix}"
    image = "ubuntu:22.04" if os_type == "ubuntu" else "debian:bookworm"
    container_id = await async_docker_run(image, hostname, ram, cpu, disk, container_name)
    if not container_id:
        embed = discord.Embed(description="Failed to create Docker container.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    await asyncio.sleep(5)  # Wait for container to start
    await async_install_tmate(container_id, os_type)
    await asyncio.sleep(10)  # Wait for install
    exec_process = await docker_exec_tmate(container_id)
    ssh_line = await capture_ssh_session_line(exec_process)
    if ssh_line:
        add_vps(user_id, container_id, container_name, os_type, hostname, ssh_line, ram, cpu, disk)
        os_name = "Ubuntu 22.04" if os_type == "ubuntu" else "Debian 12"
        embed = discord.Embed(title="VPS Instance Created", description=f"OS: {os_name}\nRAM: {ram} | CPU: {cpu} | Disk: {disk}\n```{ssh_line}```", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
        try:
            await target_user.send(embed=embed)
        except discord.Forbidden:
            logger.warning(f"Cannot DM user {target_user.id} for creation")
        embed_success = discord.Embed(description="Your VPS is ready! Check your DMs for access details.", color=discord.Color.green())
        await interaction.followup.send(embed=embed_success, ephemeral=True)
    else:
        embed = discord.Embed(description="Creation failed: Unable to generate SSH session.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        await async_docker_stop(container_id)
        await asyncio.sleep(2)
        await async_docker_rm(container_id)

# Admin helpers
async def admin_manage_vps(interaction: discord.Interaction, target_user_id: int, vps_identifier: str, action: str):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    target_user = await bot.fetch_user(target_user_id)
    if not target_user:
        embed = discord.Embed(description="User not found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        return
    vps = get_vps_by_identifier(target_user_id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="VPS not found for this user.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        return
    container_id = vps['container_id']
    success = False
    if action == "delete":
        await async_docker_stop(container_id)
        await asyncio.sleep(2)
        await async_docker_rm(container_id)
        delete_vps(container_id)
        success = True
        msg = f"Deleted VPS for {target_user}"
    elif action in ["start", "stop", "restart"]:
        if action == "start":
            success = await async_docker_start(container_id)
            update_vps_status(container_id, "running")
        elif action == "stop":
            success = await async_docker_stop(container_id)
            update_vps_status(container_id, "stopped")
        elif action == "restart":
            success = await async_docker_restart(container_id)
            update_vps_status(container_id, "running")
        msg = f"{action.title()}ed VPS for {target_user}"
    elif action == "suspend":
        success = await async_docker_stop(container_id)
        if success:
            update_vps_status(container_id, "stopped")
            update_vps_suspended(container_id, 1)
        msg = f"Suspended VPS for {target_user}"
    elif action == "unsuspend":
        update_vps_suspended(container_id, 0)
        success = True
        msg = f"Unsuspended VPS for {target_user}. You can now start it."
    if success:
        embed = discord.Embed(title="Admin Action Completed", description=msg, color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(description="Action failed.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)

async def admin_kill_all(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT container_id FROM vps WHERE status = "running"')
    running = cursor.fetchall()
    conn.close()
    stopped = 0
    for row in running:
        cid = row['container_id']
        if await async_docker_stop(cid):
            update_vps_status(cid, "stopped")
            stopped += 1
            logger.info(f"Stopped {cid}")
    embed = discord.Embed(title="Admin: Kill All Running VPS", description=f"Successfully stopped {stopped} running VPS instances.", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="admin-list", description="Admin: List all VPS instances")
@app_commands.guild_only()
async def admin_list(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.username, v.container_id, v.container_name, v.os_type, v.hostname, v.status, v.ram, v.cpu, v.disk, v.suspended
        FROM vps v JOIN users u ON v.user_id = u.user_id
        ORDER BY v.created_at DESC
    ''')
    all_vps = cursor.fetchall()
    conn.close()
    if not all_vps:
        embed = discord.Embed(description="No VPS instances found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        return
    embed = discord.Embed(title="All VPS Instances", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    for row in all_vps[:25]:
        username = row['username']
        container_id = row['container_id']
        container_name = row['container_name']
        os_type = row['os_type']
        hostname = row['hostname']
        status = row['status']
        ram = row['ram']
        cpu = row['cpu']
        disk = row['disk']
        suspended = row['suspended']
        status_emoji = "🟢" if status == "running" else "🔴"
        suspended_text = "(Suspended)" if suspended else ""
        embed.add_field(
            name=f"{status_emoji} {username} - {container_name} ({os_type}) {suspended_text}",
            value=f"ID: ```{container_id}```\nHostname: {hostname}\nStatus: {status}\nResources: {ram} RAM | {cpu} CPU | {disk} Disk",
            inline=False
        )
    if len(all_vps) > 25:
        embed.set_footer(text=f"{WATERMARK} | Showing first 25 of {len(all_vps)}", icon_url=bot.user.avatar.url if bot.user.avatar else None)
    else:
        embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="admin-list-users", description="Admin: List users with VPS counts")
@app_commands.guild_only()
async def admin_list_users(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.username, COUNT(v.id) as total_vps,
               SUM(CASE WHEN v.status = 'running' THEN 1 ELSE 0 END) as running_vps
        FROM users u LEFT JOIN vps v ON u.user_id = v.user_id
        GROUP BY u.user_id, u.username
        ORDER BY total_vps DESC
    ''')
    users = cursor.fetchall()
    conn.close()
    if not users:
        embed = discord.Embed(description="No users found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        return
    embed = discord.Embed(title="Users Overview", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    for row in users[:25]:
        username = row['username']
        total = row['total_vps']
        running = row['running_vps'] or 0
        embed.add_field(
            name=username,
            value=f"Total VPS: {total} | Running: {running}",
            inline=False
        )
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="admin-stats", description="Admin: View bot statistics")
@app_commands.guild_only()
async def admin_stats(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    num_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM vps')
    num_vps = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM vps WHERE status="running"')
    num_running = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM bans')
    num_banned = cursor.fetchone()[0]
    cursor.execute('SELECT ram, cpu, disk FROM vps WHERE status="running"')
    rows = cursor.fetchall()
    total_cpu = sum(float(row['cpu']) for row in rows)
    total_ram = sum(parse_gb(row['ram']) for row in rows)
    total_disk = sum(parse_gb(row['disk']) for row in rows)
    conn.close()
    embed = discord.Embed(title="Bot Statistics", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.add_field(name="Total Users", value=num_users, inline=True)
    embed.add_field(name="Banned Users", value=num_banned, inline=True)
    embed.add_field(name="Total VPS", value=num_vps, inline=True)
    embed.add_field(name="Running VPS", value=num_running, inline=True)
    embed.add_field(name="Total CPU Allocated", value=f"{total_cpu} cores", inline=True)
    embed.add_field(name="Total RAM Allocated", value=f"{total_ram:.1f} GB", inline=True)
    embed.add_field(name="Total Disk Allocated", value=f"{total_disk:.1f} GB", inline=True)
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="admin-delete-user", description="Admin: Delete all VPS for a user")
@app_commands.describe(target_user="The target user")
@app_commands.guild_only()
async def admin_delete_user(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer()
    user_id = target_user.id
    vps_list = get_user_vps(user_id)
    deleted = 0
    for vps in vps_list:
        container_id = vps['container_id']
        await async_docker_stop(container_id)
        await asyncio.sleep(2)
        await async_docker_rm(container_id)
        delete_vps(container_id)
        deleted += 1
        logger.info(f"Deleted VPS {container_id} for user {user_id}")
    embed = discord.Embed(description=f"Deleted {deleted} VPS instances for {target_user}.", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="admin-ban", description="Admin: Ban a user from creating VPS")
@app_commands.describe(target_user="The target user")
@app_commands.guild_only()
async def admin_ban(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    add_ban(target_user.id)
    embed = discord.Embed(description=f"Banned {target_user} from creating VPS instances.", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="admin-unban", description="Admin: Unban a user")
@app_commands.describe(target_user="The target user")
@app_commands.guild_only()
async def admin_unban(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    remove_ban(target_user.id)
    embed = discord.Embed(description=f"Unbanned {target_user}.", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="admin-vps-info", description="Admin: View full VPS details for a user")
@app_commands.describe(target_user="The target user", vps_identifier="VPS ID or Name")
@app_commands.guild_only()
async def admin_vps_info(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="VPS not found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        return
    container_id = vps['container_id']
    uptime = get_uptime(container_id)
    stats = get_stats(container_id)
    os_name = "Ubuntu 22.04" if vps['os_type'] == "ubuntu" else "Debian 12"
    embed = discord.Embed(title=f"{target_user.name} - VPS Details: {vps['container_name']}", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.add_field(name="OS", value=os_name, inline=True)
    embed.add_field(name="Hostname", value=vps['hostname'], inline=True)
    embed.add_field(name="Status", value=vps['status'], inline=True)
    embed.add_field(name="Suspended", value="Yes" if vps['suspended'] else "No", inline=True)
    embed.add_field(name="Container ID", value=f"```{container_id}```", inline=False)
    embed.add_field(name="Allocated Resources", value=f"{vps['ram']} RAM | {vps['cpu']} CPU | {vps['disk']} Disk", inline=False)
    embed.add_field(name="Current Usage", value=f"CPU: {stats['cpu']} | Mem: {stats['mem']}", inline=False)
    embed.add_field(name="Uptime", value=uptime, inline=True)
    embed.add_field(name="Network I/O", value=stats['net'], inline=False)
    embed.add_field(name="Created At", value=vps['created_at'], inline=True)
    if vps['ssh_command']:
        ssh_trunc = vps['ssh_command'][:100] + "..." if len(vps['ssh_command']) > 100 else vps['ssh_command']
        embed.add_field(name="SSH Command", value=f"```{ssh_trunc}```", inline=False)
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="admin-logs", description="Admin: View logs for a user's VPS")
@app_commands.describe(target_user="The target user", vps_identifier="VPS ID or Name", lines="Number of lines (default 50)")
@app_commands.guild_only()
async def admin_logs(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str, lines: int = 50):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="VPS not found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        return
    container_id = vps['container_id']
    logs = get_logs(container_id, lines)
    embed = discord.Embed(title=f"Logs for {target_user.name}'s {vps['container_name']}", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.add_field(name="Recent Logs", value=f"```{logs}```", inline=False)
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed)

# Show bot & developer information
@Hopingboyz)\n"
            "💻 **GitHub:** [View Projects & Scripts](https://github.com/Hopingboyz)\n"
            "📸 **Instagram:** [Follow for Updates](https://instagram.com/hopingboyz)"
        ),
        inline=False
    )

    # Fun Fact / Extra Touch
    embed.add_field(
        name="🎮 Fun Fact",
        value=(
            "Hopingboyz is also a big **Minecraft** fan! Many tutorials cover free/paid hosting, "
            "server setups, web stores, and getting powerful VPS resources for gaming servers 🟩"
        ),
        inline=False
    )

    embed.set_footer(
        text="Built with ❤️ and ☕ by Hopingboyz | Thank you for using VPS Manager Bot!",
        icon_url="https://i.imgur.com/qziVuLk.jpeg"  # Suggested: A profile-related image from YouTube
    )
    embed.set_thumbnail(
        url="https://i.imgur.com/BIbPOCV.jpeg"  # A cool Discord bot / VPS themed thumbnail for better visuals
    )
    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="logs", description="View recent logs for your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name", lines="Number of lines (default 50)")
async def user_logs(interaction: discord.Interaction, vps_identifier: str, lines: int = 50):
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="VPS not found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    container_id = vps['container_id']
    logs = get_logs(container_id, lines)
    embed = discord.Embed(title=f"Logs for {vps['container_name']}", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.add_field(name="Recent Logs", value=f"```{logs}```", inline=False)
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Slash Commands
@bot.tree.command(name="deploy", description="Deploy a new VPS instance with default resources")
@app_commands.describe(os_type="The OS type for the VPS")
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu", value="ubuntu"),
    app_commands.Choice(name="Debian", value="debian")
])
async def deploy(interaction: discord.Interaction, os_type: str):
    await create_vps(interaction, os_type)

@bot.tree.command(name="admin-create", description="Admin: Create a VPS for a user with optional custom resources")
@app_commands.describe(target_user="The target user", os_type="OS type", ram="RAM e.g. 2g (optional)", cpu="CPU cores (optional)", disk="Disk e.g. 20G (optional)")
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu", value="ubuntu"),
    app_commands.Choice(name="Debian", value="debian")
])
async def admin_create(interaction: discord.Interaction, target_user: discord.User, os_type: str, ram: str = None, cpu: str = None, disk: str = None):
    if not is_admin(interaction.user):
        embed = discord.Embed(description="This command is restricted to admins only.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    ram = ram or DEFAULT_RAM
    cpu = cpu or DEFAULT_CPU
    disk = disk or DEFAULT_DISK
    if get_total_instances() >= TOTAL_SERVER_LIMIT:
        embed = discord.Embed(description=f"Global server limit reached: {TOTAL_SERVER_LIMIT} total running instances.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await create_vps(interaction, os_type, ram, cpu, disk, target_user=target_user)

@bot.tree.command(name="vps-info", description="View full details of your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name (defaults to first)")
async def vps_info(interaction: discord.Interaction, vps_identifier: str = None):
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="No VPS found.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    container_id = vps['container_id']
    uptime = get_uptime(container_id)
    stats = get_stats(container_id)
    os_name = "Ubuntu 22.04" if vps['os_type'] == "ubuntu" else "Debian 12"
    embed = discord.Embed(title=f"VPS Details: {vps['container_name']}", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.add_field(name="OS", value=os_name, inline=True)
    embed.add_field(name="Hostname", value=vps['hostname'], inline=True)
    embed.add_field(name="Status", value=vps['status'], inline=True)
    embed.add_field(name="Suspended", value="Yes" if vps['suspended'] else "No", inline=True)
    embed.add_field(name="Container ID", value=f"```{container_id}```", inline=False)
    embed.add_field(name="Allocated Resources", value=f"{vps['ram']} RAM | {vps['cpu']} CPU | {vps['disk']} Disk", inline=False)
    embed.add_field(name="Current Usage", value=f"CPU: {stats['cpu']} | Mem: {stats['mem']}", inline=False)
    embed.add_field(name="Uptime", value=uptime, inline=True)
    embed.add_field(name="Network I/O", value=stats['net'], inline=False)
    embed.add_field(name="Created At", value=vps['created_at'], inline=True)
    if vps['ssh_command']:
        ssh_trunc = vps['ssh_command'][:100] + "..." if len(vps['ssh_command']) > 100 else vps['ssh_command']
        embed.add_field(name="SSH Command", value=f"```{ssh_trunc}```", inline=False)
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="regen-ssh", description="Regenerate SSH session for your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name (defaults to first)")
async def regen_ssh(interaction: discord.Interaction, vps_identifier: str = None):
    await regen_ssh_command(interaction, vps_identifier)

@bot.tree.command(name="start", description="Start your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name")
async def start_vps(interaction: discord.Interaction, vps_identifier: str):
    await manage_vps(interaction, vps_identifier, "start")

@bot.tree.command(name="stop", description="Stop your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name")
async def stop_vps(interaction: discord.Interaction, vps_identifier: str):
    await manage_vps(interaction, vps_identifier, "stop")

@bot.tree.command(name="restart", description="Restart your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name")
async def restart_vps(interaction: discord.Interaction, vps_identifier: str):
    await manage_vps(interaction, vps_identifier, "restart")

@bot.tree.command(name="reinstall", description="Reinstall your VPS with a new OS")
@app_commands.describe(vps_identifier="VPS ID or Name", os_type="The new OS type")
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu", value="ubuntu"),
    app_commands.Choice(name="Debian", value="debian")
])
async def reinstall(interaction: discord.Interaction, vps_identifier: str, os_type: str = "ubuntu"):
    await reinstall_vps(interaction, vps_identifier, os_type)

@bot.tree.command(name="list", description="List all your VPS instances")
async def list_vps(interaction: discord.Interaction):
    vps_list = get_user_vps(interaction.user.id)
    if not vps_list:
        embed = discord.Embed(description="You have no VPS instances.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    embed = discord.Embed(title="Your VPS Instances", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    for vps in vps_list[:25]:
        status_emoji = "🟢" if vps['status'] == "running" else "🔴"
        uptime = get_uptime(vps['container_id'])
        suspended_text = "(Suspended)" if vps['suspended'] else ""
        embed.add_field(
            name=f"{status_emoji} {vps['container_name']} ({vps['os_type']}) {suspended_text}",
            value=f"ID: ```{vps['container_id']}```\nHostname: {vps['hostname']}\nStatus: {vps['status']}\nUptime: {uptime}\nResources: {vps['ram']} RAM | {vps['cpu']} CPU | {vps['disk']} Disk",
            inline=False
        )
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remove", description="Remove your VPS instance")
@app_commands.describe(vps_identifier="VPS ID or Name")
async def remove_vps(interaction: discord.Interaction, vps_identifier: str):
    await interaction.response.defer(ephemeral=True)
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="VPS not found.", color=discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    container_id = vps['container_id']
    await async_docker_stop(container_id)
    await asyncio.sleep(2)
    await async_docker_rm(container_id)
    delete_vps(container_id)
    embed = discord.Embed(title="VPS Removed Successfully", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.followup.send(embed=embed, ephemeral=True)

# Admin commands
@bot.tree.command(name="admin-manage", description="Admin: Manage a user's VPS (start/stop/restart/delete/suspend/unsuspend)")
@app_commands.describe(target_user="The target user", vps_identifier="VPS ID or Name", action="The action to perform")
@app_commands.choices(action=[
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="restart", value="restart"),
    app_commands.Choice(name="delete", value="delete"),
    app_commands.Choice(name="suspend", value="suspend"),
    app_commands.Choice(name="unsuspend", value="unsuspend")
])
@app_commands.guild_only()
async def admin_manage(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str, action: str):
    await interaction.response.defer()
    await admin_manage_vps(interaction, target_user.id, vps_identifier, action)

@bot.tree.command(name="admin-kill-all", description="Admin: Stop all running VPS instances")
@app_commands.guild_only()
async def admin_kill_all_cmd(interaction: discord.Interaction):
    await admin_kill_all(interaction)

@bot.tree.command(name="ping", description="Check the bot's latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(title="🏓 Pong!", description=f"Latency: {latency}ms", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="View help and command list")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="VPS Bot Help", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.set_footer(text=WATERMARK, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.add_field(name="**User Commands**", value="", inline=False)
    embed.add_field(name="/deploy <os>", value="Deploy a new VPS with default resources (Ubuntu or Debian)", inline=False)
    embed.add_field(name="/list", value="List all your VPS instances with details", inline=False)
    embed.add_field(name="/vps-info [vps_id]", value="View full details of a VPS including usage and SSH", inline=False)
    embed.add_field(name="/start <vps_id>", value="Start a VPS", inline=False)
    embed.add_field(name="/stop <vps_id>", value="Stop a VPS", inline=False)
    embed.add_field(name="/restart <vps_id>", value="Restart a VPS", inline=False)
    embed.add_field(name="/regen-ssh [vps_id]", value="Regenerate SSH session", inline=False)
    embed.add_field(name="/reinstall <vps_id> [os]", value="Reinstall VPS with new OS (keeps resources)", inline=False)
    embed.add_field(name="/remove <vps_id>", value="Remove a VPS", inline=False)
    embed.add_field(name="/about", value="Show bot & developer information", inline=False)
    embed.add_field(name="/logs <vps_id> [lines]", value="View recent VPS logs", inline=False)
    if ADMIN_ID > 0:
        embed.add_field(name="**Admin Commands**", value="", inline=False)
        embed.add_field(name="/admin-create <user> <os> [ram] [cpu] [disk]", value="Create VPS for a user with optional resources", inline=False)
        embed.add_field(name="/admin-manage <user> <vps> <action>", value="Manage user's VPS (start/stop/restart/delete/suspend/unsuspend)", inline=False)
        embed.add_field(name="/admin-list-users", value="List users with VPS counts", inline=False)
        embed.add_field(name="/admin-list", value="List all VPS instances", inline=False)
        embed.add_field(name="/admin-stats", value="View bot statistics", inline=False)
        embed.add_field(name="/admin-vps-info <user> <vps>", value="View full details for a user's VPS", inline=False)
        embed.add_field(name="/admin-logs <user> <vps> [lines]", value="View logs for a user's VPS", inline=False)
        embed.add_field(name="/admin-delete-user <user>", value="Delete all VPS for a user", inline=False)
        embed.add_field(name="/admin-ban <user>", value="Ban a user from creating VPS", inline=False)
        embed.add_field(name="/admin-unban <user>", value="Unban a user", inline=False)
        embed.add_field(name="/admin-kill-all", value="Stop all running VPS instances", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tasks.loop(minutes=5)
async def sync_statuses():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT container_id, status FROM vps')
    for row in cursor.fetchall():
        cid = row['container_id']
        stat = row['status']
        try:
            out = subprocess.check_output(["docker", "inspect", "-f", "{{.State.Status}}", cid]).decode().strip()
            if out != stat:
                update_vps_status(cid, out)
                logger.info(f"Updated status of {cid} to {out}")
        except subprocess.CalledProcessError:
            if stat != "stopped":
                update_vps_status(cid, "stopped")
                logger.info(f"Updated non-existent {cid} to stopped")
        except Exception as e:
            logger.error(f"Status sync error for {cid}: {e}")
    conn.close()

# Events
@bot.event
async def on_ready():
    change_status.start()
    sync_statuses.start()
    logger.info(f'Bot ready: {bot.user}')
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} commands')
    except Exception as e:
        logger.error(f'Sync failed: {e}')

@tasks.loop(seconds=10)
async def change_status():
    try:
        count = get_total_instances()
        status = f"{BOT_STATUS_NAME} | {count} Active"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        logger.error(f"Status update failed: {e}")

if __name__ == "__main__":
    if not TOKEN:
        logger.error("TOKEN not set in .env")
        sys.exit(1)
    bot.run(TOKEN)