#!/usr/bin/env python3
"""
Procedural LEMP + WordPress installer for Ubuntu 20.04+.

Features:
 - Procedural (no custom classes) with stdlib only
 - Dry-run (default) and execute modes
 - Full-overwrite option (Option A) that removes existing webroot/nginx config/database/user
 - Full cleanup on failure (Option A)
 - Queries WordPress version API and downloads actual latest release
 - Atomic writes for configs
 - Safety checks before actions
 - Signal handling to cleanup on interrupts
 - MySQL socket authentication (passwordless root access via sudo, no remote root login)

Use:
  Dry run (review actions):
    sudo ./lemp_wp_installer.py --domain example.com --db-pass WpPass123 --dry-run

  Execute:
    sudo ./lemp_wp_installer.py --domain example.com --db-pass WpPass123 --execute --certbot --overwrite
"""

from __future__ import annotations
import argparse
import datetime
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

# ---------------------------
# Enhanced logging configuration
# ---------------------------
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    MAGENTA = '\033[0;35m'
    NC = '\033[0m'

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors and enhanced formatting"""
    
    FORMATS = {
        logging.DEBUG: f"{Colors.CYAN}[%(asctime)s] [DEBUG] [%(funcName)s:%(lineno)d]{Colors.NC} %(message)s",
        logging.INFO: f"{Colors.BLUE}[%(asctime)s] [INFO]{Colors.NC} %(message)s",
        logging.WARNING: f"{Colors.YELLOW}[%(asctime)s] [WARNING] [%(funcName)s:%(lineno)d]{Colors.NC} %(message)s",
        logging.ERROR: f"{Colors.RED}[%(asctime)s] [ERROR] [%(funcName)s:%(lineno)d]{Colors.NC} %(message)s",
        logging.CRITICAL: f"{Colors.MAGENTA}[%(asctime)s] [CRITICAL] [%(funcName)s:%(lineno)d]{Colors.NC} %(message)s",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%Y-%m-%d %H:%M:%S')
        return formatter.format(record)

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)

def debug(msg: str, **kwargs):
    """Debug level logging with optional structured data"""
    if kwargs:
        msg += f" | {kwargs}"
    logger.debug(msg)

def info(msg: str, **kwargs):
    """Info level logging with optional structured data"""
    if kwargs:
        msg += f" | {kwargs}"
    logger.info(msg)

def success(msg: str, **kwargs):
    """Success messages (info level with green color)"""
    if kwargs:
        msg += f" | {kwargs}"
    print(f"{Colors.GREEN}[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [SUCCESS]{Colors.NC} {msg}")

def warning(msg: str, **kwargs):
    """Warning level logging with optional structured data"""
    if kwargs:
        msg += f" | {kwargs}"
    logger.warning(msg)

def error(msg: str, **kwargs):
    """Error level logging with optional structured data"""
    if kwargs:
        msg += f" | {kwargs}"
    logger.error(msg)

def dry_run_print(desc: str, cmd: str = ""):
    """Print dry-run actions"""
    print(f"{Colors.YELLOW}[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [DRY-RUN]{Colors.NC} {desc}")
    if cmd:
        print(f"  Command: {cmd}")

# ---------------------------
# Globals / state
# ---------------------------
state: Dict[str, object] = {
    "created_paths": [],  # for cleanup
    "created_db": False,
    "db_name": None,
    "db_user": None,
    "nginx_conf_path": None,
    "web_root": None,
    "tmp_dirs": [],
    "executing": False,
    "overwrite": False,
}

# ---------------------------
# Utility functions
# ---------------------------

def run_cmd(cmd: str, capture_output: bool = False, check: bool = True, shell: bool = True, dry_run: bool = True) -> subprocess.CompletedProcess:
    """Run a system command. In dry-run mode we only print what would be executed."""
    debug(f"run_cmd called", cmd=cmd[:100], capture=capture_output, check=check, dry_run=dry_run)
    
    if dry_run and not state.get("executing", False):
        dry_run_print("Would run command", cmd)
        # Return a dummy CompletedProcess
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
    
    info(f"Executing command: {cmd[:120]}{'...' if len(cmd) > 120 else ''}")
    start_time = time.time()
    
    try:
        result = subprocess.run(cmd, shell=shell, check=check, capture_output=capture_output, text=True)
        duration = time.time() - start_time
        
        debug(f"Command completed", returncode=result.returncode, duration_sec=f"{duration:.2f}")
        
        if result.stdout and not capture_output:
            debug(f"Command stdout: {result.stdout[:500]}")
        if result.stderr:
            warning(f"Command stderr: {result.stderr[:500]}")
            
        return result
    except subprocess.CalledProcessError as e:
        duration = time.time() - start_time
        error(f"Command failed", returncode=e.returncode, duration_sec=f"{duration:.2f}")
        if e.stdout:
            error(f"Failed command stdout: {e.stdout[:500]}")
        if e.stderr:
            error(f"Failed command stderr: {e.stderr[:500]}")
        raise

def ensure_root(dry_run: bool):
    debug("Checking root privileges", dry_run=dry_run, euid=os.geteuid())
    if dry_run:
        warning("Dry-run mode: not enforcing root privileges")
        return
def ensure_root(dry_run: bool):
    debug("Checking root privileges", dry_run=dry_run, euid=os.geteuid())
    if dry_run:
        warning("Dry-run mode: not enforcing root privileges")
        return
    if os.geteuid() != 0:
        error("This script must be run as root (use sudo). Exiting.")
        sys.exit(1)
    info("Root privileges confirmed")

def timestamp() -> str:
    return datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")

def atomic_write(path: Path, data: str, mode: int = 0o644, dry_run: bool = True):
    """Write data to path atomically (via tmp file and os.replace)."""
    p = Path(path)
    debug(f"atomic_write called", path=str(p), size_bytes=len(data), mode=oct(mode), dry_run=dry_run)
    
    if dry_run and not state.get("executing", False):
        dry_run_print(f"Would write file {p} ({len(data)} bytes)")
        return
    
    tmp = p.parent / (".tmp." + p.name + "." + timestamp())
    debug(f"Creating temporary file", tmp_path=str(tmp))
    tmp.parent.mkdir(parents=True, exist_ok=True)
    
    with open(tmp, "w") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, p)
    
    info(f"Wrote {p} ({len(data)} bytes)")
    debug(f"File write complete", final_path=str(p), mode=oct(mode))

def detect_php_version(dry_run: bool) -> str:
    """Try to detect installed PHP major.minor, otherwise query apt-cache, else default to 8.1"""
    debug("Detecting PHP version", dry_run=dry_run)
    
    # If php installed, query php -r
    try:
        debug("Attempting to detect PHP via php binary")
        out = subprocess.run(['php', '-r', 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;'], capture_output=True, text=True, check=True)
        ver = out.stdout.strip()
        info(f"Detected PHP version via php binary: {ver}")
        return ver
    except Exception as e:
        debug(f"PHP binary detection failed", error=str(e))
    
    # apt-cache policy
    try:
        debug("Attempting to detect PHP via apt-cache policy")
        out = subprocess.run(['apt-cache', 'policy', 'php-fpm'], capture_output=True, text=True, check=True)
        m = re.search(r'Candidate:.*php(\d+\.\d+)', out.stdout)
        if m:
            ver = m.group(1)
            info(f"Detected PHP version via apt-cache: {ver}")
            return ver
    except Exception as e:
        debug(f"apt-cache policy detection failed", error=str(e))
    
    # search apt
    try:
        debug("Attempting to detect PHP via apt-cache search")
        out = subprocess.run(['apt-cache', 'search', '--names-only', '^php[0-9]+\\.[0-9]+-fpm$'], capture_output=True, text=True, check=True)
        versions = re.findall(r'php(\d+\.\d+)-fpm', out.stdout)
        if versions:
            ver = sorted(versions)[-1]
            info(f"Using latest available php-fpm in apt cache: {ver}")
            debug(f"Available PHP versions found", versions=versions)
            return ver
    except Exception as e:
        debug(f"apt-cache search detection failed", error=str(e))
    
    warning("Falling back to PHP 8.1")
    return "8.1"

def fetch_latest_wordpress_download_url(timeout: int = 15) -> Tuple[str, str]:
    """
    Query WordPress version-check API and return (version, download_url).
    If the API fails, fallback to https://wordpress.org/latest.tar.gz (version 'latest').
    """
    api = "https://api.wordpress.org/core/version-check/1.7/"
    debug(f"Fetching WordPress version info", api=api, timeout=timeout)
    
    try:
        info("Querying WordPress version API...")
        start_time = time.time()
        with urllib.request.urlopen(api, timeout=timeout) as resp:
            duration = time.time() - start_time
            data = json.load(resp)
            debug(f"WordPress API response received", duration_sec=f"{duration:.2f}")
            
            # data['offers'] is a list; hopefully first is latest
            offers = data.get("offers") 
            if offers and isinstance(offers, list):
                offer = offers[0]
                version = offer.get("current", "latest")
                download = offer.get("download")
                if download:
                    info(f"Latest WordPress version: {version}")
                    debug(f"WordPress download URL", url=download, version=version)
                    return version, download
    except Exception as e:
        warning(f"Failed to get WP version via API: {e}")
        debug(f"WordPress API error details", error=str(e), error_type=type(e).__name__)

    fallback = "https://wordpress.org/latest.tar.gz"
    warning("Falling back to wordpress.org/latest.tar.gz")
    return "latest", fallback

def download_and_extract_wp(download_url: str, dest: Path, dry_run: bool, keep_wp_content: bool = False) -> None:
    """
    Download WordPress tarball and extract files into dest.
    If dest exists and overwrite==True then it's removed prior to extraction (handled by caller).
    """
    debug(f"download_and_extract_wp called", url=download_url, dest=str(dest), dry_run=dry_run)
def download_and_extract_wp(download_url: str, dest: Path, dry_run: bool, keep_wp_content: bool = False) -> None:
    """
    Download WordPress tarball/zip and extract files into dest.
    If dest exists and overwrite==True then it's removed prior to extraction (handled by caller).
    """
    debug(f"download_and_extract_wp called", url=download_url, dest=str(dest), dry_run=dry_run)
    tmpdir = Path(tempfile.mkdtemp(prefix="wpdl_"))
    state["tmp_dirs"].append(str(tmpdir))
    
    # Determine file type from URL
    is_zip = download_url.endswith('.zip')
    archive_ext = '.zip' if is_zip else '.tar.gz'
    archive_path = tmpdir / f"wp{archive_ext}"
    debug(f"Created temporary directory", tmpdir=str(tmpdir), archive_type='zip' if is_zip else 'tar.gz')

    if dry_run and not state.get("executing", False):
        dry_run_print(f"Would download WordPress from {download_url} to {archive_path}")
        return

    info(f"Downloading WordPress from {download_url} ...")
    start_time = time.time()
    try:
        urllib.request.urlretrieve(download_url, str(archive_path))
        duration = time.time() - start_time
        file_size = archive_path.stat().st_size
        info(f"Download complete", size_mb=f"{file_size / 1024 / 1024:.2f}", duration_sec=f"{duration:.2f}")
    except Exception as e:
        error(f"Failed to download WordPress archive", url=download_url, error=str(e))
        raise RuntimeError(f"Failed to download WordPress archive: {e}")

    # Extract to tmpdir/extracted
    extracted = tmpdir / "extracted"
    extracted.mkdir()
    debug(f"Extracting archive", source=str(archive_path), target=str(extracted), format='zip' if is_zip else 'tar.gz')
    
    start_time = time.time()
    try:
        if is_zip:
            # Handle ZIP files
            with zipfile.ZipFile(archive_path, 'r') as zf:
                zf.extractall(path=str(extracted))
        else:
            # Handle tar.gz files
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(path=str(extracted))
        
        duration = time.time() - start_time
        info(f"Extraction complete", duration_sec=f"{duration:.2f}", format='zip' if is_zip else 'tar.gz')
    except zipfile.BadZipFile as e:
        error(f"Failed to extract WordPress zip file", error=str(e))
        raise RuntimeError(f"Failed to extract WordPress zip file: {e}")
    except tarfile.TarError as e:
        error(f"Failed to extract WordPress tarball", error=str(e))
        raise RuntimeError(f"Failed to extract WordPress tarball: {e}")
    except Exception as e:
        error(f"Failed to extract WordPress archive", error=str(e), error_type=type(e).__name__)
        raise RuntimeError(f"Failed to extract WordPress archive: {e}")

    wp_root = None
    for entry in extracted.iterdir():
        if entry.is_dir() and entry.name.lower().startswith("wordpress"):
            wp_root = entry
            debug(f"Found WordPress root directory", path=str(wp_root))
            break
    if not wp_root:
        # maybe archive contains files directly
        wp_root = extracted
        debug("Using extracted directory directly as WordPress root")

    info(f"Copying WordPress files to {dest} ...")
    dest.mkdir(parents=True, exist_ok=True)
    
    start_time = time.time()
    file_count = 0
    # copytree + overwrite behavior: copy files from wp_root/* into dest
    for item in wp_root.iterdir():
        target = dest / item.name
        debug(f"Copying", source=item.name, target=str(target), is_dir=item.is_dir())
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        file_count += 1
    
    duration = time.time() - start_time
    info(f"WordPress files copied", file_count=file_count, duration_sec=f"{duration:.2f}")

    # Set ownership and perms will be handled by caller
    success("WordPress downloaded and extracted")

def mysql_exec(sql: str, mysql_root_pass: str, dry_run: bool = True) -> None:
    """Execute SQL as root. Note: we pass via mysql client."""
    debug(f"mysql_exec called", sql_preview=sql[:100], dry_run=dry_run)
def mysql_exec(sql: str, mysql_root_pass: str = None, dry_run: bool = True) -> None:
    """Execute SQL as root using socket authentication (passwordless)."""
    debug(f"mysql_exec called", sql_preview=sql[:100], dry_run=dry_run)
    # build command list to avoid shell quoting pitfalls
    if dry_run and not state.get("executing", False):
        dry_run_print("Would run MySQL statement", f"sudo mysql -u root -e \"{sql[:100]}...\"")
        return
    # Use sudo mysql for socket authentication (no password needed)
    cmd = ["sudo", "mysql", "-u", "root", "-e", sql]
    info(f"Executing MySQL: {sql.strip()[:120]}...")
    start_time = time.time()
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        duration = time.time() - start_time
        debug(f"MySQL command completed", duration_sec=f"{duration:.2f}")
        if result.stdout:
            debug(f"MySQL stdout: {result.stdout[:200]}")
    except subprocess.CalledProcessError as e:
        duration = time.time() - start_time
        error(f"MySQL command failed", duration_sec=f"{duration:.2f}", stderr=e.stderr[:200])
        raise

def backup_path(path: Path) -> Optional[Path]:
    """If path exists, move it to a .bak-TIMESTAMP and return new path"""
    debug(f"backup_path called", path=str(path), exists=path.exists())
    if not path.exists():
        return None
    bak = path.with_name(f"{path.name}.bak-{timestamp()}")
    shutil.move(str(path), str(bak))
    info(f"Backed up {path} -> {bak}")
    return bak

def remove_path(path: Path, dry_run: bool = True):
    debug(f"remove_path called", path=str(path), exists=path.exists(), dry_run=dry_run)
def remove_path(path: Path, dry_run: bool = True):
    debug(f"remove_path called", path=str(path), exists=path.exists(), dry_run=dry_run)
    if not path.exists():
        debug(f"Path does not exist, skipping removal", path=str(path))
        return
    if dry_run and not state.get("executing", False):
        dry_run_print("Would remove", str(path))
        return
    
    start_time = time.time()
    if path.is_dir():
        shutil.rmtree(path)
        duration = time.time() - start_time
        info(f"Removed directory {path}", duration_sec=f"{duration:.2f}")
    else:
        path.unlink()
        duration = time.time() - start_time
        info(f"Removed file {path}", duration_sec=f"{duration:.2f}")

# ---------------------------
# Step functions
# ---------------------------

def show_plan(cfg: Dict):
    debug("show_plan called", config_keys=list(cfg.keys()))
def show_plan(cfg: Dict):
    debug("show_plan called", config_keys=list(cfg.keys()))
    php_source = "installed" if Path("/usr/bin/php").exists() else "APT"
    print("\n" + "=" * 50)
    info("LEMP + WordPress installation plan")
    print("=" * 50)
    print(f"Mode: {'DRY-RUN (no changes)' if cfg['dry_run'] else 'EXECUTE'}")
    print(f"Domain: {cfg['domain']}")
    print(f"Email: {cfg['email']}")
    print(f"Install SSL (certbot): {cfg['certbot']}")
    print(f"Web root: {cfg['web_root']}")
    print(f"Nginx config: {cfg['nginx_conf']}")
    print(f"MySQL root: Socket authentication (passwordless via sudo)")
    print(f"DB name: {cfg['db_name']}")
    print(f"DB user: {cfg['db_user']}")
    print(f"PHP version (detected): {cfg['php_version']} ({php_source})")
    print(f"Overwrite enabled: {cfg['overwrite']}")
    print("=" * 50 + "\n")

def step_update_system(cfg: Dict):
    info("Step 1: Updating system packages")
    debug("Starting system package update")
    run_cmd("apt update && apt upgrade -y", dry_run=cfg["dry_run"])
    success("System packages updated")

def step_install_nginx(cfg: Dict):
    info("Step 2: Install and start Nginx")
    debug("Installing Nginx package")
    run_cmd("apt install -y nginx", dry_run=cfg["dry_run"])
    debug("Enabling Nginx service")
    run_cmd("systemctl enable nginx", dry_run=cfg["dry_run"])
    debug("Starting Nginx service")
    run_cmd("systemctl start nginx", dry_run=cfg["dry_run"])
    success("Nginx installed and started")

def step_install_mysql(cfg: Dict):
    info("Step 3: Install MySQL server and secure it")
    debug("Installing MySQL server package")
def step_install_mysql(cfg: Dict):
    info("Step 3: Install MySQL server and secure it")
    debug("Installing MySQL server package")
    run_cmd("apt install -y mysql-server", dry_run=cfg["dry_run"])
    
    # secure installation steps
    if cfg["dry_run"]:
        dry_run_print("Would secure MySQL (ensure socket authentication for root and remove anonymous users / test db)")
        return

    debug("Securing MySQL installation")
    
    # Ensure root uses socket authentication (auth_socket plugin) - passwordless for local sudo access
    # This disables password-based root login and enables socket authentication
    try:
        debug("Configuring MySQL root for socket authentication (passwordless local access)")
        subprocess.run(
            ["sudo", "mysql", "-e", 
             "ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket;"],
            check=False, capture_output=True, text=True
        )
        info("MySQL root configured for socket authentication (passwordless with sudo)")
    except Exception as e:
        warning(f"Failed to configure socket authentication: {e}")

    # Now run cleanup queries via mysql_exec (no password needed with socket auth)
    try:
        debug("Removing anonymous MySQL users")
        mysql_exec("DELETE FROM mysql.user WHERE User='';", dry_run=cfg["dry_run"])
        
        debug("Removing remote root access - ensuring root can only connect from localhost via socket")
        mysql_exec("DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost');", dry_run=cfg["dry_run"])
        
        debug("Dropping test database")
        mysql_exec("DROP DATABASE IF EXISTS test;", dry_run=cfg["dry_run"])
        
        debug("Removing test database privileges")
        mysql_exec("DELETE FROM mysql.db WHERE Db='test' OR Db LIKE 'test\\_%';", dry_run=cfg["dry_run"])
        
        debug("Flushing MySQL privileges")
        mysql_exec("FLUSH PRIVILEGES;", dry_run=cfg["dry_run"])
        
        success("MySQL secured with socket authentication (root login only via sudo, no remote access)")
    except subprocess.CalledProcessError as e:
        error(f"MySQL secure commands failed", error=str(e))
        raise RuntimeError(f"MySQL secure commands failed: {e}")

def step_install_php(cfg: Dict):
    info("Step 4: Install PHP and extensions")
    debug(f"Installing PHP {cfg['php_version']} and extensions")
def step_install_php(cfg: Dict):
    info("Step 4: Install PHP and extensions")
    debug(f"Installing PHP {cfg['php_version']} and extensions")
    pkgs = "php-fpm php-mysql php-curl php-gd php-mbstring php-xml php-xmlrpc php-soap php-intl php-zip"
    run_cmd(f"apt install -y {pkgs}", dry_run=cfg["dry_run"])
    
    php_ini = Path(f"/etc/php/{cfg['php_version']}/fpm/php.ini")
    debug(f"Configuring PHP settings", ini_path=str(php_ini))
    
    # update php.ini settings
    if cfg["dry_run"]:
        dry_run_print(f"Would edit {php_ini} to update upload_max_filesize/post_max_size/memory_limit/max_execution_time")
    else:
        if not php_ini.exists():
            warning(f"{php_ini} not found; skipping php.ini edits")
        else:
            debug(f"Reading PHP ini file", path=str(php_ini))
            with open(php_ini, "r") as f:
                content = f.read()
            
            original_size = len(content)
            content = re.sub(r'upload_max_filesize\s*=\s*.*', 'upload_max_filesize = 64M', content)
            content = re.sub(r'post_max_size\s*=\s*.*', 'post_max_size = 64M', content)
            content = re.sub(r'memory_limit\s*=\s*.*', 'memory_limit = 256M', content)
            content = re.sub(r'max_execution_time\s*=\s*.*', 'max_execution_time = 300', content)
            
            debug(f"PHP ini modifications complete", original_bytes=original_size, new_bytes=len(content))
            atomic_write(php_ini, content, mode=0o644, dry_run=cfg["dry_run"])
    
    debug(f"Restarting PHP-FPM service")
    run_cmd(f"systemctl restart php{cfg['php_version']}-fpm", dry_run=cfg["dry_run"])
    success("PHP installed and configured")

def step_create_database(cfg: Dict):
    info("Step 5: Create WordPress database and user")
    db = cfg["db_name"]
    user = cfg["db_user"]
    passwd = cfg["db_pass"]
    
    debug(f"Database creation requested", db_name=db, db_user=user)
    state["db_name"] = db
    state["db_user"] = user

    if cfg["overwrite"]:
        # remove existing database and user first
        info("Overwrite requested: dropping existing database and user if they exist")
        debug(f"Dropping database and user", db=db, user=user)
        sql = f"DROP DATABASE IF EXISTS `{db}`; DROP USER IF EXISTS '{user}'@'localhost'; FLUSH PRIVILEGES;"
        mysql_exec(sql, dry_run=cfg["dry_run"])

    debug(f"Creating database and user")
    sql = f"""
CREATE DATABASE IF NOT EXISTS `{db}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '{user}'@'localhost' IDENTIFIED BY '{passwd}';
GRANT ALL PRIVILEGES ON `{db}`.* TO '{user}'@'localhost';
FLUSH PRIVILEGES;
"""
    mysql_exec(sql, dry_run=cfg["dry_run"])
    state["created_db"] = True
    success("Database creation step complete", db=db, user=user)

def step_download_and_install_wp(cfg: Dict):
    info("Step 6: Download and install WordPress")
    webroot = Path(cfg["web_root"])
    state["web_root"] = str(webroot)
    
    debug(f"WordPress installation requested", webroot=str(webroot))

    version, download_url = fetch_latest_wordpress_download_url()
    info(f"Will download WordPress version: {version} from {download_url}")

    if webroot.exists():
        debug(f"Webroot already exists", path=str(webroot), overwrite=cfg["overwrite"])
        if cfg["overwrite"]:
            info("Overwrite enabled: removing existing web root")
            remove_path(webroot, dry_run=cfg["dry_run"])
        else:
            error(f"Web root already exists and overwrite not enabled", path=str(webroot))
            raise RuntimeError(f"Web root {webroot} already exists. Use --overwrite to replace.")

    # create web root
    if cfg["dry_run"] and not state.get("executing", False):
        dry_run_print(f"Would create web root directory {webroot}")
    else:
        debug(f"Creating web root directory", path=str(webroot))
        webroot.mkdir(parents=True, exist_ok=True)
        state["created_paths"].append(str(webroot))

    download_and_extract_wp(download_url, webroot, dry_run=cfg["dry_run"])

    # configure wp-config.php
    wp_sample = webroot / "wp-config-sample.php"
    wp_conf = webroot / "wp-config.php"
    
    debug(f"Configuring WordPress", sample=str(wp_sample), config=str(wp_conf))
    
    if wp_sample.exists():
        if cfg["dry_run"] and not state.get("executing", False):
            dry_run_print(f"Would create wp-config.php from {wp_sample}")
        else:
            debug(f"Reading wp-config-sample.php")
            with open(wp_sample, "r") as f:
                conf = f.read()
            
            debug(f"Replacing database credentials in wp-config")
            conf = conf.replace("database_name_here", cfg["db_name"])
            conf = conf.replace("username_here", cfg["db_user"])
            conf = conf.replace("password_here", cfg["db_pass"])

            # Get salts from WP API
            try:
                info("Fetching WordPress salts from API...")
                start_time = time.time()
                with urllib.request.urlopen("https://api.wordpress.org/secret-key/1.1/salt/") as resp:
                    salts = resp.read().decode("utf-8")
                duration = time.time() - start_time
                debug(f"WordPress salts fetched", duration_sec=f"{duration:.2f}", salts_length=len(salts))
                # Replace the block of salts between /**#@+ ... */ with the returned salts
                # Simpler: replace placeholder lines AUTH_KEY...NONCE_SALT block if present
                # We'll attempt to locate lines starting with define('AUTH_KEY' and ending with define('NONCE_SALT'
                conf = re.sub(r"(define\(\s*'AUTH_KEY'.*?define\(\s*'NONCE_SALT'.*?\);\s*)", salts + "\n", conf, flags=re.DOTALL)
            except Exception as e:
                warning(f"Failed to fetch salts from WP API: {e}; leaving sample salts (not ideal)")
            atomic_write(wp_conf, conf, dry_run=cfg["dry_run"])

    # set ownership and permissions
    debug(f"Setting file ownership and permissions")
    run_cmd(f"chown -R www-data:www-data {webroot}", dry_run=cfg["dry_run"])
    run_cmd(f"find {webroot} -type d -exec chmod 755 {{}} \\;", dry_run=cfg["dry_run"])
    run_cmd(f"find {webroot} -type f -exec chmod 644 {{}} \\;", dry_run=cfg["dry_run"])
    success("WordPress files installed and permissions set")

def step_configure_nginx(cfg: Dict):
    info("Step 7: Create Nginx site configuration for WordPress")
    nginx_conf_path = Path(cfg["nginx_conf"])
    state["nginx_conf_path"] = str(nginx_conf_path)
    
    debug(f"Configuring Nginx", conf_path=str(nginx_conf_path), domain=cfg['domain'])
def step_configure_nginx(cfg: Dict):
    info("Step 7: Create Nginx site configuration for WordPress")
    nginx_conf_path = Path(cfg["nginx_conf"])
    state["nginx_conf_path"] = str(nginx_conf_path)
    
    debug(f"Configuring Nginx", conf_path=str(nginx_conf_path), domain=cfg['domain'])

    nginx_conf = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {cfg['domain']} www.{cfg['domain']};

    root {cfg['web_root']};
    index index.php index.html index.htm;

    # Upload size limits (matches PHP settings)
    client_max_body_size 64M;

    location / {{
        try_files $uri $uri/ /index.php?$args;
    }}

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php{cfg['php_version']}-fpm.sock;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include fastcgi_params;
    }}

    location ~ /\\.ht {{
        deny all;
    }}

    location = /favicon.ico {{
        log_not_found off;
        access_log off;
    }}

    location = /robots.txt {{
        allow all;
        log_not_found off;
        access_log off;
    }}

    location ~* \\.(css|gif|ico|jpeg|jpg|js|png)$ {{
        expires max;
        log_not_found off;
    }}
}}
"""

    # If nginx conf exists and overwrite requested, remove it
    if nginx_conf_path.exists():
        debug(f"Nginx config already exists", path=str(nginx_conf_path), overwrite=cfg["overwrite"])
        if cfg["overwrite"]:
            info("Overwrite: backing up and removing existing nginx config")
            backup_path(nginx_conf_path)
        else:
            error(f"Nginx config already exists and overwrite not enabled", path=str(nginx_conf_path))
            raise RuntimeError(f"Nginx config {nginx_conf_path} exists. Use --overwrite to replace.")

    # atomic write
    debug(f"Writing Nginx configuration", config_size=len(nginx_conf))
    atomic_write(nginx_conf_path, nginx_conf, dry_run=cfg["dry_run"])

    # symlink to sites-enabled
    debug(f"Creating symlink to sites-enabled")
    run_cmd(f"ln -sf {nginx_conf_path} /etc/nginx/sites-enabled/", dry_run=cfg["dry_run"])
    
    debug(f"Removing default Nginx site")
    run_cmd("rm -f /etc/nginx/sites-enabled/default", dry_run=cfg["dry_run"])
    
    debug(f"Testing Nginx configuration")
    run_cmd("nginx -t", dry_run=cfg["dry_run"])
    
    debug(f"Reloading Nginx")
    run_cmd("systemctl reload nginx", dry_run=cfg["dry_run"])

    success("Nginx configured for site")

def step_install_ssl(cfg: Dict):
    if not cfg["certbot"]:
        info("SSL installation skipped (certbot not requested)")
        return
    info("Step 8: Install Certbot and obtain Let's Encrypt certificate")
    
    debug(f"Installing Certbot", domain=cfg['domain'])
    run_cmd("apt install -y certbot python3-certbot-nginx", dry_run=cfg["dry_run"])
    
    if cfg["dry_run"]:
        dry_run_print("Would run certbot to obtain/renew certificates for domain", f"certbot --nginx -d {cfg['domain']} -d www.{cfg['domain']} --non-interactive --agree-tos -m {cfg['email']} --redirect")
    else:
        debug(f"Running Certbot for domain", domain=cfg['domain'], email=cfg['email'])
        run_cmd(f"certbot --nginx -d {cfg['domain']} -d www.{cfg['domain']} --non-interactive --agree-tos -m {cfg['email']} --redirect", dry_run=False)
        success("SSL certificate installed")

def show_summary(cfg: Dict):
    debug("Showing installation summary")
    print("\n" + "=" * 50)
    info("Installation summary")
    print("=" * 50)
    if cfg["dry_run"]:
        info("DRY-RUN complete. No changes were made.")
    else:
        success("Installation steps finished (check for errors above).")
    print(f"Domain: {cfg['domain']}")
    print(f"Web root: {cfg['web_root']}")
    print(f"Nginx config: {cfg['nginx_conf']}")
    print(f"Database: {cfg['db_name']} (user: {cfg['db_user']})")
    if not cfg["dry_run"] and cfg["certbot"]:
        print(f"HTTPS: https://{cfg['domain']}")
    print("=" * 50 + "\n")

# ---------------------------
# Cleanup on failure / normal
# ---------------------------

def cleanup_full(cfg: Dict):
    """
    Full cleanup: removes created webroot, nginx conf, tmp dirs and drops DB/user if created.
    Used for Option A (full cleanup).
    """
    warning("Running full cleanup (Option A)...")
    debug(f"Full cleanup initiated", dry_run=cfg.get("dry_run", True))
    
    # remove web root if it was created
    webroot = Path(cfg["web_root"])
    if webroot.exists():
        debug(f"Removing web root during cleanup", path=str(webroot))
        remove_path(webroot, dry_run=cfg["dry_run"])

    # remove nginx conf & symlink
    nginx_conf_path = Path(cfg["nginx_conf"])
    symlink = Path("/etc/nginx/sites-enabled") / nginx_conf_path.name
    
    if symlink.exists():
        debug(f"Removing Nginx symlink during cleanup", path=str(symlink))
        remove_path(symlink, dry_run=cfg["dry_run"])
    
    if nginx_conf_path.exists():
        # back it up first (don't remove original silently), but for full cleanup we remove it
        debug(f"Removing Nginx config during cleanup", path=str(nginx_conf_path))
        remove_path(nginx_conf_path, dry_run=cfg["dry_run"])

    # drop DB and user if we created them or overwrite was requested
    if cfg["overwrite"] or state.get("created_db"):
        db = cfg["db_name"]
        user = cfg["db_user"]
        debug(f"Dropping database and user during cleanup", db=db, user=user)
        try:
            mysql_exec(f"DROP DATABASE IF EXISTS `{db}`; DROP USER IF EXISTS '{user}'@'localhost'; FLUSH PRIVILEGES;", dry_run=cfg["dry_run"])
        except Exception as e:
            warning(f"Failed to drop DB/user during cleanup: {e}")

    # remove tmp dirs
    tmp_count = len(state.get("tmp_dirs", []))
    debug(f"Removing temporary directories", count=tmp_count)
    for td in state.get("tmp_dirs", []):
        try:
            tdp = Path(td)
            if tdp.exists():
                remove_path(tdp, dry_run=cfg["dry_run"])
        except Exception as e:
            debug(f"Failed to remove temp dir", path=td, error=str(e))

    success("Cleanup complete")

# ---------------------------
# Signal handler
# ---------------------------

def signal_handler(sig, frame):
    warning(f"Received signal {sig}. Attempting cleanup...")
    debug(f"Signal handler invoked", signal=sig)
    # cfg is not globally available here; store minimal cfg in state
    cfg = state.get("last_cfg", {})
    try:
        cleanup_full(cfg)
    except Exception as e:
        error(f"Cleanup failed during signal handling: {e}")
    sys.exit(1)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ---------------------------
# Main runner
# ---------------------------

def main():
    debug("Application starting")
def main():
    debug("Application starting")
    parser = argparse.ArgumentParser(description="Procedural LEMP + WordPress Installer")
    parser.add_argument("-d", "--domain", required=True, help="Domain name (example.com)")
    parser.add_argument("-e", "--email", help="Admin email for certbot (default admin@domain)")
    parser.add_argument("-n", "--db-name", default="wordpress", help="WordPress DB name (default wordpress)")
    parser.add_argument("-u", "--db-user", default="wpuser", help="WordPress DB user (default wpuser)")
    parser.add_argument("-p", "--db-pass", required=True, help="WordPress DB password")
    parser.add_argument("-c", "--certbot", action="store_true", help="Install and configure Let's Encrypt SSL (certbot)")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", dest="dry_run", action="store_true", help="Show what would be done (default)")
    mode_group.add_argument("--execute", dest="dry_run", action="store_false", help="Actually execute the installation")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing install/configs and DB (FULL overwrite - Option A)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    # Set logging level based on verbose flag
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        debug("Verbose logging enabled")
    else:
        logger.setLevel(logging.INFO)

    # default dry-run if not provided
    if args.dry_run is None:
        args.dry_run = True

    debug(f"Parsed arguments", domain=args.domain, db_name=args.db_name, dry_run=args.dry_run, overwrite=args.overwrite)

    cfg = {
        "domain": args.domain,
        "email": args.email or f"admin@{args.domain}",
        "db_name": args.db_name,
        "db_user": args.db_user,
        "db_pass": args.db_pass,
        "certbot": args.certbot,
        "dry_run": args.dry_run,
        "overwrite": args.overwrite,
    }

    # populate detected php version
    debug("Detecting PHP version")
    cfg["php_version"] = detect_php_version(cfg["dry_run"])

    cfg["web_root"] = f"/var/www/{cfg['domain']}"
    cfg["nginx_conf"] = f"/etc/nginx/sites-available/{cfg['domain']}"

    # stash last cfg for signal handler and cleanup usage
    state["last_cfg"] = cfg
    state["overwrite"] = cfg["overwrite"]

    debug(f"Configuration prepared", web_root=cfg["web_root"], nginx_conf=cfg["nginx_conf"])

    show_plan(cfg)

    # confirm in execute mode
    if not cfg["dry_run"]:
        resp = input("Continue with installation? (y/N) ")
        if resp.strip().lower() != "y":
            warning("Installation cancelled by user.")
            sys.exit(0)

    ensure_root(cfg["dry_run"])
    # mark executing only when actually doing changes to allow dry_run prints
    state["executing"] = not cfg["dry_run"]
    
    if state["executing"]:
        info("Starting installation in EXECUTE mode")
    else:
        info("Starting installation in DRY-RUN mode")

    # Run steps with try/except to cleanup on failure (Option A full cleanup)
    overall_start = time.time()
    try:
        step_update_system(cfg)
        step_install_nginx(cfg)
        step_install_mysql(cfg)
        step_install_php(cfg)
        step_create_database(cfg)
        step_download_and_install_wp(cfg)
        step_configure_nginx(cfg)
        step_install_ssl(cfg)
        
        overall_duration = time.time() - overall_start
        info(f"All installation steps completed", total_duration_sec=f"{overall_duration:.2f}")
        show_summary(cfg)
    except Exception as exc:
        overall_duration = time.time() - overall_start
        error(f"Installation failed", error=str(exc), duration_sec=f"{overall_duration:.2f}")
        debug(f"Exception details", exception_type=type(exc).__name__)
        try:
            cleanup_full(cfg)
        except Exception as e:
            error(f"Cleanup encountered errors: {e}")
        sys.exit(1)
    finally:
        # always attempt to remove temp dirs created during download in execute/dry-run modes
        debug(f"Cleaning up temporary directories")
        for td in state.get("tmp_dirs", []):
            try:
                tdp = Path(td)
                if tdp.exists():
                    if cfg["dry_run"]:
                        dry_run_print("Would remove temporary dir", str(tdp))
                    else:
                        shutil.rmtree(tdp)
                        debug(f"Removed temporary directory", path=str(tdp))
            except Exception as e:
                debug(f"Failed to remove temp dir in finally", path=td, error=str(e))

if __name__ == "__main__":
    main()
