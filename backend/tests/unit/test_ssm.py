from datetime import datetime, timezone

from backend.ssl_monitor.adapters.ssm import SSMProbe
from backend.ssl_monitor.core.models import DomainRecord


class FakeSSM:
    def __init__(self):
        self.commands = []

    def send_command(self, **kwargs):
        self.commands.append(kwargs)
        return {"Command": {"CommandId": "cmd-1"}}

    def get_command_invocation(self, **kwargs):
        return {
            "Status": "Success",
            "StandardOutputContent": (
                '{"reachable": true, "not_after": '
                '"2030-01-01T00:00:00+00:00", "error": null}\n'
            ),
        }


def test_ssm_probe_runs_on_record_instance_and_parses_certificate():
    client = FakeSSM()
    probe = SSMProbe(client, poll_interval_seconds=0)
    record = DomainRecord.new("private.example", 443, datetime.now(timezone.utc))
    record.agent_id = "mi-0123456789abcdef0"

    result = probe(record, 5)

    assert result.reachable is True
    assert result.not_after is not None
    assert client.commands[0]["InstanceIds"] == [record.agent_id]
    assert client.commands[0]["DocumentName"] == "AWS-RunShellScript"
    assert client.commands[0]["TimeoutSeconds"] >= 30
    assert "private.example" in client.commands[0]["Parameters"]["commands"][0]