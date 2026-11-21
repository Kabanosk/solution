"""New workspace Slack authorizations store stable channel IDs, not mutable names."""
from datetime import datetime, timezone

from scout.connectors.chat import SlackChat


class ChannelClient:
    def __init__(self):
        self.read = []

    def conversations_list(self, **kwargs):
        return {"channels": [
            {"id": "C12345678", "name": "renamed-finance", "is_member": True},
            {"id": "C87654321", "name": "not-authorized", "is_member": True},
        ]}

    def conversations_history(self, *, channel, **kwargs):
        self.read.append(channel)
        assert channel == "C12345678", "A channel outside the owner's allowlist was read"
        return {"messages": [{"text": "Invoice INV-001 is filed", "ts": "1732100000", "user": "U123"}]}

    def chat_getPermalink(self, **kwargs):
        return {"permalink": "https://example.slack.com/archives/C12345678/p1732100000"}


def test_owner_selected_channel_ids_remain_readable_after_a_slack_channel_is_renamed():
    client = ChannelClient()
    chat = SlackChat(client, ["C12345678"])
    hits = chat.search(since=datetime(2026, 1, 1, tzinfo=timezone.utc), invoice_number="INV-001")
    assert len(hits) == 1
    assert hits[0].channel == "renamed-finance"
    assert client.read == ["C12345678"]
