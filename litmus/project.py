import requests

from litmus import const


def get_project_id() -> str:
    return \
        requests.get(f'http://{const.url}/auth/list_projects',
                     headers={'Authorization': f'Bearer {const.token}'}).json()[
            'data'][0]['ID']
    pass
