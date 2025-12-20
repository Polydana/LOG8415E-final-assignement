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
    """
    return f"""#!/bin/bash
set -xe
exec > /var/log/mysql-manager-user-data.log 2>&1

# Update system and install MySQL + tools
apt-get update -y
apt-get install -y mysql-server sysbench git wget unzip

# Enable and start MySQL
systemctl enable mysql
systemctl start mysql

# Set root password and allow root client config
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"

cat <<EOF >/root/.my.cnf
[client]
user=root
password={config.MYSQL_ROOT_PASSWORD}
EOF
chmod 600 /root/.my.cnf

# === Allow remote connections: bind to all interfaces ===
# Default Ubuntu conf usually has: bind-address = 127.0.0.1
# We switch it to 0.0.0.0 so other instances in the VPC can connect.
if grep -q "^bind-address" /etc/mysql/mysql.conf.d/mysqld.cnf; then
  sed -i "s/^bind-address.*/bind-address = 0.0.0.0/" /etc/mysql/mysql.conf.d/mysqld.cnf
else
  echo "bind-address = 0.0.0.0" >> /etc/mysql/mysql.conf.d/mysqld.cnf
fi

# === Create replication user and Sakila/Proxy users, allowed from any host ===
# Replication user
mysql -e "CREATE USER IF NOT EXISTS '{config.MYSQL_REPL_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_REPL_PASSWORD}';"
mysql -e "GRANT REPLICATION SLAVE ON *.* TO '{config.MYSQL_REPL_USER}'@'%';"

# Sakila / Proxy user (used by the proxy app)
mysql -e "CREATE USER IF NOT EXISTS '{config.MYSQL_SAKILA_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_SAKILA_PASSWORD}';"
mysql -e "GRANT ALL PRIVILEGES ON *.* TO '{config.MYSQL_SAKILA_USER}'@'%' WITH GRANT OPTION;"

# (Optional but very useful) allow root to connect from any host too
mysql -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}';"
mysql -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;"

mysql -e "FLUSH PRIVILEGES;"

# === Download and install Sakila database ===
cd /tmp
wget https://downloads.mysql.com/docs/sakila-db.tar.gz
tar xzf sakila-db.tar.gz
cd sakila-db
mysql -e "SOURCE sakila-schema.sql;"
mysql sakila < sakila-data.sql

# === Configure MySQL for replication (master) ===
cat <<EOF >> /etc/mysql/mysql.conf.d/mysqld.cnf

# LOG8415E replication master
server-id       = 1
log_bin         = /var/log/mysql/mysql-bin.log
binlog_do_db    = sakila
EOF

# Restart MySQL to apply bind-address + replication settings
systemctl restart mysql

"""

def render_mysql_worker_user_data(server_id: int, manager_private_ip: str) -> str:
    """
    User-data script for a MySQL worker (replica).
    - Installs MySQL
    - Configures as replication slave
    - Connects to manager
    """
    return f"""#!/bin/bash
set -xe
exec > /var/log/mysql-worker-{server_id}-user-data.log 2>&1

apt-get update -y
apt-get install -y mysql-server git

systemctl enable mysql
systemctl start mysql

# Set root password
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"

cat <<EOF >/root/.my.cnf
[client]
user=root
password={config.MYSQL_ROOT_PASSWORD}
EOF
chmod 600 /root/.my.cnf

# Configure as replication slave + bind to all interfaces
cat <<EOF >> /etc/mysql/mysql.conf.d/mysqld.cnf

# LOG8415E replication slave
server-id = {server_id}
relay-log = /var/log/mysql/mysql-relay-bin.log
binlog_do_db = sakila
bind-address = 0.0.0.0
EOF

systemctl restart mysql

# === Create same proxy/sakila users on the workers ===
mysql -e "CREATE USER IF NOT EXISTS '{config.MYSQL_SAKILA_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_SAKILA_PASSWORD}';"
mysql -e "GRANT ALL PRIVILEGES ON *.* TO '{config.MYSQL_SAKILA_USER}'@'%' WITH GRANT OPTION;"

mysql -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}';"
mysql -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;"

mysql -e "FLUSH PRIVILEGES;"

# Make sure the sakila database exists (it will be replicated, but create empty db just in case)
mysql -e "CREATE DATABASE IF NOT EXISTS sakila;"

# Setup replication pointing to manager
mysql -e "CHANGE REPLICATION SOURCE TO \\
  SOURCE_HOST='{manager_private_ip}', \\
  SOURCE_USER='{config.MYSQL_REPL_USER}', \\
  SOURCE_PASSWORD='{config.MYSQL_REPL_PASSWORD}', \\
  SOURCE_AUTO_POSITION=1;"

mysql -e "START REPLICA;"

"""



def render_proxy_user_data(manager_ip: str, worker_ips: List[str]) -> str:
    """
    User-data for Proxy instance:
    - Installs Python + git
    - Clones repo
    - Installs requirements
    - Exports env vars for proxy app
    - Waits for MySQL manager to be ready
    - Starts proxy.app
    """
    worker_ips_str = ",".join(worker_ips)

    return f"""#!/bin/bash
set -xe
exec > /var/log/proxy-user-data.log 2>&1

apt-get update -y
apt-get install -y python3 python3-pip git mysql-client

cd /home/ubuntu
if [ ! -d "LOG8415E-final-assignement" ]; then
  git clone {config.GIT_REPO_URL} LOG8415E-final-assignement
fi
cd {config.REMOTE_PROJECT_PATH}

pip3 install -r requirements.txt

export MANAGER_HOST="{manager_ip}"
export WORKER_HOSTS="{worker_ips_str}"
export DB_USER="{config.MYSQL_SAKILA_USER}"
export DB_PASSWORD="{config.MYSQL_SAKILA_PASSWORD}"
export DB_NAME="sakila"
export DB_PORT="3306"
export PROXY_STRATEGY="direct"
export DEBUG="false"

echo "Waiting for MySQL manager at {manager_ip}:3306..."
for i in {{1..30}}; do
  if mysql -h {manager_ip} -P 3306 -u{config.MYSQL_SAKILA_USER} -p{config.MYSQL_SAKILA_PASSWORD} -e "SELECT 1" sakila >/dev/null 2>&1; then
    echo "MySQL is ready."
    break
  fi
  echo "MySQL not ready yet, retrying in 5s..."
  sleep 5
done

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
