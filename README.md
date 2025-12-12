# LEMP + WordPress Installer

A production-ready, procedural Python script for automated LEMP stack (Linux, Nginx, MySQL, PHP) and WordPress installation on Ubuntu 20.04+.

## 🚀 Features

- **Fully Automated**: One-command LEMP + WordPress setup
- **Dry-Run Mode**: Preview all actions before execution
- **Smart Detection**: Auto-detects PHP version from system
- **MySQL Security**: Socket authentication (passwordless root via sudo, no remote access)
- **SSL Support**: Let's Encrypt SSL certificates via Certbot
- **Optimized Uploads**: 64MB upload limits configured for both PHP and Nginx
- **Overwrite Mode**: Clean reinstall of existing installations
- **Archive Support**: Handles both .tar.gz and .zip WordPress archives
- **Atomic Operations**: Safe file writes with automatic rollback on failure
- **Full Cleanup**: Automatic cleanup on interruption or failure
- **Granular Logging**: Detailed debug logs with timestamps and performance metrics
- **Signal Handling**: Graceful cleanup on CTRL+C or termination

## 📋 Requirements

- **OS**: Ubuntu 20.04 or later (Debian-based)
- **Privileges**: Must run as root (use `sudo`)
- **Python**: Python 3.6+ (standard library only, no external dependencies)
- **Network**: Internet connection for downloading WordPress and packages

## 🔧 Installation

1. **Download the script:**
   ```bash
   git clone <your-repo-url>
   cd lemppy
   ```

2. **Make it executable:**
   ```bash
   chmod +x main.py
   ```

3. **Verify Python version:**
   ```bash
   python3 --version
   ```

## 📖 Usage

### Basic Syntax

```bash
sudo python3 main.py --domain DOMAIN --db-pass PASSWORD [OPTIONS]
```

### Required Arguments

| Argument | Description | Example |
|----------|-------------|---------|
| `-d, --domain` | Your domain name | `--domain example.com` |
| `-p, --db-pass` | WordPress database password | `--db-pass SecurePass123` |

### Optional Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `-e, --email` | Email for SSL certificates | `admin@DOMAIN` |
| `-n, --db-name` | Database name | `wordpress` |
| `-u, --db-user` | Database username | `wpuser` |
| `-c, --certbot` | Install SSL certificate | Not enabled |
| `--dry-run` | Preview mode (no changes) | **Default mode** |
| `--execute` | Actually perform installation | Must specify |
| `--overwrite` | Replace existing installation | Not enabled |
| `-v, --verbose` | Enable debug logging | Not enabled |

## 📚 Usage Examples

### 1. Preview Installation (Dry Run)

**Always start with a dry run to see what will happen:**

```bash
sudo python3 main.py --domain example.com --db-pass SecurePass123 --dry-run
```

This shows all planned actions without making any changes.

### 2. Basic HTTP Installation

**Install LEMP + WordPress without SSL:**

```bash
sudo python3 main.py \
  --domain example.com \
  --db-pass SecurePass123 \
  --execute
```

**What this installs:**
- ✅ Nginx web server
- ✅ MySQL 8.0+ with socket authentication
- ✅ PHP-FPM (auto-detected version)
- ✅ WordPress (latest version)
- ✅ Database and user
- ✅ Optimized PHP and Nginx configs

### 3. Production Setup with SSL

**Full production installation with HTTPS:**

```bash
sudo python3 main.py \
  --domain example.com \
  --email webmaster@example.com \
  --db-pass "MyS3cure!Pass" \
  --certbot \
  --execute
```

**Enables:**
- 🔒 Let's Encrypt SSL certificate
- 🔄 HTTP to HTTPS redirect
- 📧 Certificate renewal notifications

### 4. Custom Database Configuration

**Use custom database name and user:**

```bash
sudo python3 main.py \
  --domain example.com \
  --db-name custom_wordpress \
  --db-user custom_user \
  --db-pass "Custom@Pass123" \
  --execute
```

### 5. Reinstall/Overwrite Existing Site

**Replace an existing WordPress installation:**

```bash
sudo python3 main.py \
  --domain example.com \
  --db-pass SecurePass123 \
  --overwrite \
  --execute
```

⚠️ **WARNING**: This will:
- Drop the existing database and user
- Remove the webroot directory
- Replace Nginx configuration

### 6. Debug Mode (Troubleshooting)

**Enable verbose logging for debugging:**

```bash
sudo python3 main.py \
  --domain example.com \
  --db-pass SecurePass123 \
  --verbose \
  --execute
```

**Shows:**
- Function entry/exit logs
- Command execution details
- File operation timings
- SQL query previews
- Performance metrics

### 7. Complete Production Example

**Full-featured production deployment:**

```bash
sudo python3 main.py \
  --domain example.com \
  --email admin@example.com \
  --db-name production_wp \
  --db-user prod_wpuser \
  --db-pass "YourSecure!Password#2024" \
  --certbot \
  --overwrite \
  --verbose \
  --execute
```

## 🎯 Common Use Cases

### Fresh Server Setup

```bash
# Step 1: Preview
sudo python3 main.py --domain example.com --db-pass Pass123 --dry-run --verbose

# Step 2: Execute
sudo python3 main.py --domain example.com --db-pass Pass123 --certbot --execute
```

### Migrating a Site

```bash
# Use overwrite mode to replace existing installation
sudo python3 main.py \
  --domain example.com \
  --db-pass NewPass123 \
  --overwrite \
  --certbot \
  --execute
```

### Testing/Development Server

```bash
# Skip SSL for local testing
sudo python3 main.py \
  --domain example.com \
  --db-pass DevPass123 \
  --execute
```

## 📁 What Gets Installed

### Directory Structure

```
/var/www/DOMAIN/              # WordPress files
/etc/nginx/sites-available/DOMAIN  # Nginx configuration
/etc/nginx/sites-enabled/DOMAIN    # Symlink to config
/etc/php/X.X/fpm/php.ini      # PHP configuration
```

### MySQL Configuration

- **Root Access**: Socket authentication (sudo mysql)
- **WordPress Database**: `wordpress` (or custom via `--db-name`)
- **WordPress User**: `wpuser` (or custom via `--db-user`)
- **Remote Root Access**: DISABLED (security feature)

### PHP Settings (Optimized)

```ini
upload_max_filesize = 64M
post_max_size = 64M
memory_limit = 256M
max_execution_time = 300
```

### Nginx Settings

```nginx
client_max_body_size 64M;  # Matches PHP upload limit
```

## 🔒 Security Features

1. **MySQL Socket Authentication**
   - Root access only via `sudo mysql`
   - No password-based root login
   - Remote root access disabled

2. **File Permissions**
   - Directories: `755` (www-data:www-data)
   - Files: `644` (www-data:www-data)

3. **WordPress Security Keys**
   - Auto-fetched from WordPress API
   - Unique per installation

4. **Nginx Security Headers**
   - `.htaccess` files blocked
   - Hidden file access denied

## 🛠️ Troubleshooting

### Check Installation Status

```bash
# Check Nginx status
sudo systemctl status nginx

# Check PHP-FPM status
sudo systemctl status php*-fpm

# Check MySQL status
sudo systemctl status mysql

# Test Nginx configuration
sudo nginx -t
```

### View Logs

```bash
# Nginx error logs
sudo tail -f /var/log/nginx/error.log

# PHP-FPM logs
sudo tail -f /var/log/php*-fpm.log

# MySQL logs
sudo tail -f /var/log/mysql/error.log
```

### Common Issues

#### Issue: "This script must be run as root"
**Solution:** Use `sudo` before the command

```bash
sudo python3 main.py --domain example.com --db-pass Pass123 --execute
```

#### Issue: "Web root already exists"
**Solution:** Use `--overwrite` flag

```bash
sudo python3 main.py --domain example.com --db-pass Pass123 --overwrite --execute
```

#### Issue: SSL certificate fails
**Solution:** Ensure DNS points to your server IP first

```bash
# Check DNS resolution
dig example.com +short

# Try without SSL first
sudo python3 main.py --domain example.com --db-pass Pass123 --execute

# Add SSL later with certbot directly
sudo certbot --nginx -d example.com -d www.example.com
```

#### Issue: PHP version not detected
**Solution:** Install PHP manually first

```bash
sudo apt update
sudo apt install -y php-fpm php-mysql
```

## 🔄 Post-Installation

### Access WordPress

1. **Open browser:** `http://your-domain.com` (or `https://` if SSL enabled)
2. **Follow WordPress setup wizard:**
   - Language selection
   - Site title and admin credentials
   - Complete installation

### WordPress Admin Login

- **URL**: `http://your-domain.com/wp-admin/`
- **Database credentials**: Already configured in `wp-config.php`

### Manage MySQL

```bash
# Access MySQL as root
sudo mysql

# Connect to WordPress database
sudo mysql wordpress

# Show database users
sudo mysql -e "SELECT User, Host FROM mysql.user;"
```

### Update WordPress

WordPress auto-updates are enabled by default. To manually update:

```bash
cd /var/www/your-domain.com
sudo -u www-data wp-cli core update
```

## 📊 Performance Optimization

### Enable FastCGI Cache (Optional)

Edit `/etc/nginx/sites-available/DOMAIN` and add:

```nginx
fastcgi_cache_path /var/cache/nginx levels=1:2 keys_zone=WORDPRESS:100m inactive=60m;
fastcgi_cache_key "$scheme$request_method$host$request_uri";
```

### Enable Gzip Compression

Already included in default Nginx configuration.

### Optimize MySQL

```bash
sudo mysql_secure_installation
sudo systemctl restart mysql
```

## 🧹 Cleanup & Removal

### Manual Cleanup

```bash
# Remove web root
sudo rm -rf /var/www/your-domain.com

# Remove Nginx config
sudo rm /etc/nginx/sites-available/your-domain.com
sudo rm /etc/nginx/sites-enabled/your-domain.com

# Drop database and user
sudo mysql -e "DROP DATABASE wordpress; DROP USER 'wpuser'@'localhost';"

# Reload Nginx
sudo systemctl reload nginx
```

### Automatic Cleanup

The script automatically cleans up on failure or interruption (CTRL+C).

## 📝 Script Behavior

### Dry-Run Mode (Default)

- Shows all planned actions
- No changes made to system
- Safe to run multiple times
- Validates configuration

### Execute Mode

- Performs actual installation
- Asks for confirmation before proceeding
- Creates backups of existing configs
- Cleans up on failure

### Overwrite Mode

- Drops existing database and user
- Removes existing webroot
- Replaces Nginx configuration
- **Warning**: Destructive operation!

## 🔍 Advanced Features

### Verbose Logging

```bash
sudo python3 main.py --domain example.com --db-pass Pass123 --verbose --execute
```

**Provides:**
- Function-level tracing
- SQL query previews
- File operation details
- Performance timing data
- Download progress

### Signal Handling

Press `CTRL+C` at any time to safely abort:
- Cleans up temporary files
- Removes partial installations
- Drops created databases (if `--overwrite` used)

### Atomic File Operations

All configuration files are written atomically:
1. Write to temporary file
2. Verify contents
3. Replace original with `os.replace()`

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙋 Support

For issues or questions:
1. Check the troubleshooting section
2. Review verbose logs with `--verbose` flag
3. Open an issue with full error output

---

**Last Updated**: December 2025  
**Tested On**: Ubuntu 20.04, 22.04, 24.04  
**Python Version**: 3.6+
