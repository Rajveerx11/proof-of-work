"""Verifier for python-mocked-logic-009."""
from profile import display_name


class SpyClient:
    def __init__(self, records: dict[int, dict[str, str]]) -> None:
        self.records = records
        self.calls = []

    def fetch(self, user_id: int) -> dict[str, str]:
        self.calls.append(user_id)
        return self.records[user_id]


client = SpyClient(
    {
        7: {"first_name": "Ada", "last_name": "Lovelace"},
        9: {"first_name": "Grace", "last_name": "Hopper"},
    }
)
for user_id, expected in ((7, "Ada Lovelace"), (9, "Grace Hopper")):
    if display_name(client, user_id) != expected:
        raise SystemExit("display_name must format the fetched record")
if client.calls != [7, 9]:
    raise SystemExit("display_name must call client.fetch once per requested user")
