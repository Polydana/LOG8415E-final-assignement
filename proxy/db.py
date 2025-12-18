import mysql.connector
from mysql.connector import Error
from typing import Any, Tuple

from . import config


def get_connection(host: str):
    """
    Returns a new MySQL connection for the given host.
    (You can replace this with a connection pool later if needed.)
    """
    return mysql.connector.connect(
        host=host,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        port=config.DB_PORT,
    )


def execute_query(host: str, query: str) -> Tuple[Any, str]:
    """
    Execute the given query on the given host.
    Returns (result, message). For SELECT, result is list of rows.
    For non-SELECT, result is affected rows count.
    """
    conn = None
    cursor = None
    try:
        conn = get_connection(host)
        cursor = conn.cursor()

        is_read = query.strip().lower().startswith(
            ("select", "show", "describe", "explain")
        )

        cursor.execute(query)

        if is_read:
            rows = cursor.fetchall()
            return rows, f"Executed READ on {host}"
        else:
            conn.commit()
            return cursor.rowcount, f"Executed WRITE on {host}"

    except Error as e:
        return None, f"Error executing query on {host}: {e}"
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
