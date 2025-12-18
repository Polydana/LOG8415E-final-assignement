# aws/config.py
import os

# AWS region & base settings
REGION = os.getenv("AWS_REGION", "us-east-1")

# From your assignment / learner lab
AMI_ID = os.getenv("AMI_ID", "ami-02029c87fa31fb148")
KEY_NAME = os.getenv("KEY_NAME", "vockey")

# Security group: you can pass via env or hardcode if prof allows
SECURITY_GROUP_ID = os.getenv("SECURITY_GROUP_ID", "sg-0d451c9576db378ab")

# EC2 instance types
INSTANCE_TYPE_MANAGER = os.getenv("INSTANCE_TYPE_MANAGER", "t2.micro")
INSTANCE_TYPE_WORKER = os.getenv("INSTANCE_TYPE_WORKER", "t2.micro")
INSTANCE_TYPE_PROXY = os.getenv("INSTANCE_TYPE_PROXY", "t2.large")
INSTANCE_TYPE_GATEKEEPER = os.getenv("INSTANCE_TYPE_GATEKEEPER", "t2.large")

# Tags
PROJECT_TAG_KEY = "Project"
PROJECT_TAG_VALUE = "LOG8415E-Final"

# MySQL / Sakila settings (used in user-data)
MYSQL_ROOT_PASSWORD = os.getenv("MYSQL_ROOT_PASSWORD", "rootpass")
MYSQL_REPL_USER = os.getenv("MYSQL_REPL_USER", "repl")
MYSQL_REPL_PASSWORD = os.getenv("MYSQL_REPL_PASSWORD", "replpass")
MYSQL_SAKILA_USER = os.getenv("MYSQL_SAKILA_USER", "sakila_user")
MYSQL_SAKILA_PASSWORD = os.getenv("MYSQL_SAKILA_PASSWORD", "sakila_pass")

# Path where your repo will live on instances
REMOTE_PROJECT_PATH = os.getenv(
    "REMOTE_PROJECT_PATH", "/home/ubuntu/LOG8415E-final-assignement"
)

# Git repo (if you deploy via git clone in user-data)
GIT_REPO_URL = os.getenv(
    "GIT_REPO_URL",
    "https://github.com/Polydana/LOG8415E-final-assignement.git"
)
