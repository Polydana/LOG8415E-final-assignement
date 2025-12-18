# aws/user_data.py
from typing import List

from . import config


def render_mysql_manager_user_data() -> str:
    """
    User-data script for the MySQL manager instance.
    - Installs MySQL + sysbench
    - Loads Sakila
    - Configures as replication master
    """
    return f"""#!/bin/bash
set -xe

# Update system
apt-get update -y

# Install utilities
apt-get install -y mysql-server sysbench git wget unzip

# Enable and start MySQL
systemctl enable mysql
systemctl start mysql

# Secure basic MySQL and create users
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"

# Allow root login with password
cat <<EOF >/root/.my.cnf
[client]
user=root
password={config.MYSQL_ROOT_PASSWORD}
EOF
chmod 600 /root/.my.cnf

# Configure MySQL for replication (log_bin, server-id, etc.)
cat <<EOF >> /etc/mysql/mysql.conf.d/mysqld.cnf

# LOG8415E replication master
server-id = 1
log_bin = /var/log/mysql/mysql-bin.log
binlog_do_db = sakila
bind-address = 0.0.0.0
EOF

systemctl restart mysql

# Download and install Sakila
cd /tmp
wget https://downloads.mysql.com/docs/sakila-db.tar.gz
tar xzf sakila-db.tar.gz
cd sakila-db
mysql -e "SOURCE sakila-schema.sql;"
mysql sakila < sakila-data.sql

# Create replication user and Sakila user (after DB exists is fine)
mysql -e "CREATE USER IF NOT EXISTS '{config.MYSQL_REPL_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_REPL_PASSWORD}';"
mysql -e "GRANT REPLICATION SLAVE ON *.* TO '{config.MYSQL_REPL_USER}'@'%';"

mysql -e "CREATE USER IF NOT EXISTS '{config.MYSQL_SAKILA_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_SAKILA_PASSWORD}';"
mysql -e "GRANT ALL PRIVILEGES ON sakila.* TO '{config.MYSQL_SAKILA_USER}'@'%'; FLUSH PRIVILEGES;"

# (Optional) sysbench prepare & run on manager to have standalone benchmark
sysbench /usr/share/sysbench/oltp_read_only.lua \\
  --mysql-db=sakila \\
  --mysql-user={config.MYSQL_SAKILA_USER} \\
  --mysql-password={config.MYSQL_SAKILA_PASSWORD} prepare

sysbench /usr/share/sysbench/oltp_read_only.lua \\
  --mysql-db=sakila \\
  --mysql-user={config.MYSQL_SAKILA_USER} \\
  --mysql-password={config.MYSQL_SAKILA_PASSWORD} run

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

apt-get update -y
apt-get install -y mysql-server git

systemctl enable mysql
systemctl start mysql

mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{config.MYSQL_ROOT_PASSWORD}'; FLUSH PRIVILEGES;"

cat <<EOF >/root/.my.cnf
[client]
user=root
password={config.MYSQL_ROOT_PASSWORD}
EOF
chmod 600 /root/.my.cnf

# Configure as replication slave
cat <<EOF >> /etc/mysql/mysql.conf.d/mysqld.cnf

# LOG8415E replication slave
server-id = {server_id}
relay-log = /var/log/mysql/mysql-relay-bin.log
binlog_do_db = sakila
bind-address = 0.0.0.0
EOF

systemctl restart mysql

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
export DB_USER="{config.MYSQL_SAKILA_USER}"
export DB_PASSWORD="{config.MYSQL_SAKILA_PASSWORD}"
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
