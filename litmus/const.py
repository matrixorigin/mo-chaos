import os

from litmus.auth import get_auth_token

# should be parametrized
url = os.getenv('LITMUS_URL')
username = os.getenv('LITMUS_USERNAME')
password = os.getenv('LITMUS_PASSWORD')
token = get_auth_token(url, username, password)
