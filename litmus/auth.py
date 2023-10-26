import requests


def get_auth_token(litmus_url: str, username: str, password: str) -> str:
    payload = {
        "username": username,
        "password": password
    }
    resp = requests.post(f"http://{litmus_url}/auth/login", json=payload)
    return resp.json()["access_token"]