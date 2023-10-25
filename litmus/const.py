import os

from litmus.auth import get_auth_token

# should be parametrized
url = os.getenv('LITMUS_URL') or 'chaos-litmus-frontend-service.litmus:9091'
username = os.getenv('LITMUS_USERNAME') or 'admin'
password = os.getenv('LITMUS_PASSWORD') or 'litmus'
token = get_auth_token(url, username, password)
