from litmus.auth import get_auth_token

# TODO should be parametrized, just temporary
url = 'localhost:9091'
username = 'admin'
password = 'litmus'
token = get_auth_token(url, username, password)
