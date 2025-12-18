# aws/ec2_utils.py
import time
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import WaiterError, ClientError

from . import config


def get_ec2_client():
    return boto3.client("ec2", region_name=config.REGION)


def create_instance(
    name: str,
    role: str,
    instance_type: str,
    user_data: str,
) -> str:
    """
    Launch a single EC2 instance and return its instance ID.
    """
    ec2 = get_ec2_client()

    tags = [
        {"Key": "Name", "Value": name},
        {"Key": config.PROJECT_TAG_KEY, "Value": config.PROJECT_TAG_VALUE},
        {"Key": "Role", "Value": role},
    ]

    try:
        resp = ec2.run_instances(
            ImageId=config.AMI_ID,
            InstanceType=instance_type,
            KeyName=config.KEY_NAME,
            SecurityGroupIds=[config.SECURITY_GROUP_ID],
            MinCount=1,
            MaxCount=1,
            UserData=user_data,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": tags,
                }
            ],
        )
    except ClientError as e:
        raise RuntimeError(f"Error launching instance {name}: {e}")

    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"[INFO] Launched instance {instance_id} ({name}, role={role})")
    return instance_id


def wait_for_instances(instance_ids: List[str]) -> None:
    """
    Wait for all given instances to reach 'running' state.
    """
    if not instance_ids:
        return

    ec2 = get_ec2_client()
    waiter = ec2.get_waiter("instance_running")
    try:
        print(f"[INFO] Waiting for instances to be running: {instance_ids}")
        waiter.wait(InstanceIds=instance_ids)
        print("[INFO] All instances are running.")
    except WaiterError as e:
        raise RuntimeError(f"Error while waiting for instances: {e}")


def get_instance_description(instance_id: str) -> Dict:
    ec2 = get_ec2_client()
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    return resp["Reservations"][0]["Instances"][0]


def get_private_ip(instance_id: str) -> str:
    desc = get_instance_description(instance_id)
    return desc["PrivateIpAddress"]


def get_public_ip(instance_id: str) -> Optional[str]:
    desc = get_instance_description(instance_id)
    return desc.get("PublicIpAddress")


def wait_for_ssh(instance_id: str, timeout: int = 600) -> None:
    """
    Optional: crude wait until public IP appears (not perfect but okay for this lab).
    """
    ec2 = get_ec2_client()
    start = time.time()
    while time.time() - start < timeout:
        desc = get_instance_description(instance_id)
        public_ip = desc.get("PublicIpAddress")
        state = desc["State"]["Name"]
        if state == "running" and public_ip:
            print(f"[INFO] Instance {instance_id} has public IP: {public_ip}")
            return
        print(f"[INFO] Waiting for public IP on {instance_id}...")
        time.sleep(10)
    raise TimeoutError(f"Timed out waiting for public IP on {instance_id}")
