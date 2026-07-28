from __future__ import annotations

from src.clients.mineru import MinerUClient


class StubResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_health_check_uses_authenticated_batch_probe():
    client = MinerUClient()
    client.api_key = "stub-token"
    client.model_version = "vlm"

    def fake_request(method, endpoint, **kwargs):
        assert method == "POST"
        assert endpoint == "file-urls/batch"
        assert kwargs["json"]["files"] == []
        return StubResponse(200, {"code": -10002, "msg": "file list is empty"})

    client._request = fake_request
    assert client.health_check() is True


def test_health_check_returns_false_on_bad_status():
    client = MinerUClient()
    client.api_key = "stub-token"
    client._request = lambda method, endpoint, **kwargs: StubResponse(401, {"message": "login required"})
    assert client.health_check() is False


def test_health_check_returns_false_on_invalid_json():
    client = MinerUClient()
    client.api_key = "stub-token"
    client._request = lambda method, endpoint, **kwargs: StubResponse(200, ValueError("bad json"))
    assert client.health_check() is False
