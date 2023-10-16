import requests


def get_auth_token(litmus_url: str, username: str, password: str) -> str:
    payload = {
        "username": username,
        "password": password
    }
    return requests.post(f"http://{litmus_url}/auth/login", json=payload).json()["access_token"]
