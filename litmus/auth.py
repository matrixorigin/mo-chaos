import requests


def get_auth_token(litmus_url: str, username: str, password: str) -> str:
    payload = {
        "username": username,
        "password": password
    }
    print(litmus_url)
    resp = requests.post(f"http://{litmus_url}/auth/login", json=payload)
    print(resp.status_code)
    return resp.json()["access_token"]