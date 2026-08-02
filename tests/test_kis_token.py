from market_alarm.collectors import kis


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"access_token": "shared-token", "expires_in": 86400}


class _Http:
    def __init__(self):
        self.posts = 0

    def post(self, *args, **kwargs):
        self.posts += 1
        return _Response()


def test_kis_clients_share_one_token_per_process(monkeypatch):
    http = _Http()
    monkeypatch.setenv("KIS_APP_KEY", "test-key")
    monkeypatch.setenv("KIS_APP_SECRET", "test-secret")
    monkeypatch.setattr(kis, "session", lambda config: http)
    kis._TOKEN_CACHE.clear()
    config = {
        "kis": {"base_url": "https://openapi.koreainvestment.com:9443"},
        "http": {"timeout_seconds": 30},
    }

    first = kis.KISClient(config)
    second = kis.KISClient(config)

    assert first.token == second.token == "shared-token"
    assert http.posts == 1
