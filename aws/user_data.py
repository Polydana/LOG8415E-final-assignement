# aws/user_data.py
from typing import List

from . import config


def render_mysql_manager_user_data() -> str:
    """
    User-data script for the MySQL manager instance.
    - Installs MySQL + sysbench
    - Loads Sakila
    - Configures as replication master
    - Allows remote connections (bind-address = 0.0.0.0)
    - Creates users for replication and Sakila access from other hosts
    - Ensures MySQL is actually running and listening
    """
    return f"""#!/bin/bash
set -xe
exec > /var/log/mysql-manager-user-data.log 2>&1

echo "=== [MANAGER] Updating system and installing MySQL + tools ==="
apt-get update -y
apt-get install -y mysql-server sysbench git wget unzip

echo "=== [MANAGER] Enabling and starting MySQL ==="
systemctl enable mysql
systemctl start mysql

echo "=== [MANAGER] Setting root password ==="
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"

cat <<EOF >/root/.my.cnf
[client]
user=root
password={config.MYSQL_ROOT_PASSWORD}
EOF
chmod 600 /root/.my.cnf

echo "=== [MANAGER] Binding MySQL to 0.0.0.0 ==="
# Default Ubuntu conf usually has: bind-address = 127.0.0.1
# We switch it to 0.0.0.0 so other instances in the VPC can connect.
if grep -q "^bind-address" /etc/mysql/mysql.conf.d/mysqld.cnf; then
  sed -i "s/^bind-address.*/bind-address = 0.0.0.0/" /etc/mysql/mysql.conf.d/mysqld.cnf
else
  echo "bind-address = 0.0.0.0" >> /etc/mysql/mysql.conf.d/mysqld.cnf
fi

echo "=== [MANAGER] Restarting MySQL after bind-address change ==="
systemctl restart mysql

echo "=== [MANAGER] Waiting for MySQL to be up (after bind-address) ==="
for i in {{1..30}}; do
  if mysqladmin ping -h 127.0.0.1 --silent; then
    echo "MySQL is up (phase 1)."
    break
  fi
  echo "MySQL not ready yet (phase 1), retrying in 5s..."
  sleep 5
done

echo "=== [MANAGER] Creating replication and Sakila/Proxy users (with remote access) ==="
# Replication user (keep using CREATE USER + GRANT, that's fine)
mysql -e "CREATE USER IF NOT EXISTS '{config.MYSQL_REPL_USER}'@'%' IDENTIFIED BY '{config.MYSQL_REPL_PASSWORD}';"
mysql -e "GRANT REPLICATION SLAVE ON *.* TO '{config.MYSQL_REPL_USER}'@'%';"

# Sakila / Proxy user (used by the proxy app) - create via GRANT IDENTIFIED BY
mysql -e "GRANT ALL PRIVILEGES ON *.* TO '{config.MYSQL_SAKILA_USER}'@'%' IDENTIFIED BY '{config.MYSQL_SAKILA_PASSWORD}' WITH GRANT OPTION;"

# Allow root from any host (backup) - also via GRANT IDENTIFIED BY
mysql -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' IDENTIFIED BY '{config.MYSQL_ROOT_PASSWORD}' WITH GRANT OPTION;"

mysql -e "FLUSH PRIVILEGES;"


echo "=== [MANAGER] Downloading and installing Sakila database ==="
cd /tmp
wget https://downloads.mysql.com/docs/sakila-db.tar.gz
tar xzf sakila-db.tar.gz
cd sakila-db
mysql -e "SOURCE sakila-schema.sql;"
mysql sakila < sakila-data.sql

echo '=== [MANAGER] Configuring MySQL as replication master ==='
cat <<EOF >> /etc/mysql/mysql.conf.d/mysqld.cnf

# LOG8415E replication master
server-id       = 1
log_bin         = /var/log/mysql/mysql-bin.log
binlog_do_db    = sakila
EOF

echo "=== [MANAGER] Restarting MySQL after replication config ==="
systemctl restart mysql

echo "=== [MANAGER] Waiting for MySQL to be up (final) ==="
for i in {{1..30}}; do
  if mysqladmin ping -h 127.0.0.1 --silent; then
    echo "MySQL is up (final)."
    break
  fi
  echo "MySQL not ready yet (final), retrying in 5s..."
  sleep 5
done

echo "=== [MANAGER] Manager user-data complete ==="
"""

def render_mysql_worker_user_data(server_id: int, manager_private_ip: str) -> str:
    """
    User-data script for a MySQL worker (replica).
    - Installs MySQL
    - Configures as replication slave
    - Creates the same DB users as the manager (sakila/proxy + root@'%')
    - Connects to manager
    - Binds to 0.0.0.0 so proxy can reach it
    """
    return f"""#!/bin/bash
set -xe
exec > /var/log/mysql-worker-{server_id}-user-data.log 2>&1

echo "=== [WORKER {server_id}] Updating system and installing MySQL ==="
apt-get update -y
apt-get install -y mysql-server git

echo "=== [WORKER {server_id}] Enabling MySQL ==="
systemctl enable mysql
systemctl start mysql

echo "=== [WORKER {server_id}] Setting root password ==="
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"

cat <<EOF >/root/.my.cnf
[client]
user=root
password={config.MYSQL_ROOT_PASSWORD}
EOF
chmod 600 /root/.my.cnf

echo "=== [WORKER {server_id}] Configuring replication + bind-address ==="
cat <<EOF >> /etc/mysql/mysql.conf.d/mysqld.cnf

# LOG8415E replication slave
server-id = {server_id}
relay-log = /var/log/mysql/mysql-relay-bin.log
binlog_do_db = sakila
bind-address = 0.0.0.0
EOF

echo "=== [WORKER {server_id}] Restarting MySQL after config ==="
systemctl restart mysql

echo "=== [WORKER {server_id}] Creating Sakila/Proxy DB users (remote access allowed) ==="
mysql -e "GRANT ALL PRIVILEGES ON *.* TO '{config.MYSQL_SAKILA_USER}'@'%' IDENTIFIED BY '{config.MYSQL_SAKILA_PASSWORD}' WITH GRANT OPTION;"

echo "=== [WORKER {server_id}] Allowing root remote access (backup) ==="
mysql -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' IDENTIFIED BY '{config.MYSQL_ROOT_PASSWORD}' WITH GRANT OPTION;"

mysql -e "FLUSH PRIVILEGES;"


echo "=== [WORKER {server_id}] Ensuring sakila DB exists (replica) ==="
mysql -e "CREATE DATABASE IF NOT EXISTS sakila;"

echo "=== [WORKER {server_id}] Configuring replication SOURCE ==="
mysql -e "CHANGE REPLICATION SOURCE TO \\
  SOURCE_HOST='{manager_private_ip}', \\
  SOURCE_USER='{config.MYSQL_REPL_USER}', \\
  SOURCE_PASSWORD='{config.MYSQL_REPL_PASSWORD}', \\
  SOURCE_AUTO_POSITION=1;"

echo "=== [WORKER {server_id}] Starting replication ==="
mysql -e "START REPLICA;"

echo "=== [WORKER {server_id}] Worker user-data complete ==="
"""


def render_proxy_user_data(manager_ip: str, worker_ips: List[str]) -> str:
    """
    User-data for Proxy instance:
    - Installs Python + git
    - Clones repo
    - Installs requirements
    - Exports env vars for proxy app
    - Starts proxy.app
    """
    worker_ips_str = ",".join(worker_ips)

    return f"""#!/bin/bash
set -xe

apt-get update -y
apt-get install -y python3 python3-pip git

cd /home/ubuntu
if [ ! -d "LOG8415E-final-assignement" ]; then
  git clone {config.GIT_REPO_URL} LOG8415E-final-assignement
fi
cd {config.REMOTE_PROJECT_PATH}

pip3 install -r requirements.txt

export MANAGER_HOST="{manager_ip}"
export WORKER_HOSTS="{worker_ips_str}"
export DB_USER="root"
export DB_PASSWORD="{config.MYSQL_ROOT_PASSWORD}"
export DB_NAME="sakila"
export DB_PORT="3306"
export PROXY_STRATEGY="direct"
export DEBUG="false"

nohup python3 -m proxy.app > /var/log/proxy.log 2>&1 &
"""


def render_gatekeeper_user_data(proxy_private_ip: str) -> str:
    """
    User-data for Gatekeeper instance:
    - Installs Python + git
    - Clones repo
    - Installs requirements
    - Exports env vars for gatekeeper app
    - Starts gatekeeper.app (which now listens on port 80)
    """
    return f"""#!/bin/bash
# Log script output for debugging
exec > /var/log/gatekeeper-user-data.log 2>&1
set -xe

apt-get update -y
apt-get install -y python3 python3-pip git

cd /home/ubuntu
if [ ! -d "LOG8415E-final-assignement" ]; then
  git clone {config.GIT_REPO_URL} LOG8415E-final-assignement
fi
cd {config.REMOTE_PROJECT_PATH}

pip3 install -r requirements.txt

export PROXY_URL="http://{proxy_private_ip}:5000/sql"
export API_TOKEN="supersecret123"
export DEBUG="false"

nohup python3 -m gatekeeper.app > /var/log/gatekeeper.log 2>&1 &
"""
