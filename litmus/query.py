from typing import Union, Dict, Any

from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport
from graphql import ExecutionResult

from litmus import const
from litmus.const import token


def query(req_str: str, params: dict) -> Union[Dict[str, Any], ExecutionResult]:
    transport = RequestsHTTPTransport(
        url=f"http://{const.url}/api/query",
        cookies={"litmus-cc-token": token}
    )

    client = Client(transport=transport, fetch_schema_from_transport=True)

    query = gql(req_str)

    return client.execute(query, variable_values=params)
