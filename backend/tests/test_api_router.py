import httpx
import pytest

from app.services import api_router


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _patch_client(monkeypatch, response=None, raise_exc=None):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None):
            if raise_exc:
                raise raise_exc
            return response

    monkeypatch.setattr(httpx, "Client", _FakeClient)


def test_call_api_success(monkeypatch):
    _patch_client(monkeypatch, response=_FakeResponse(200, {"id": "inv_1"}))
    cap = api_router.API_CAPABILITIES["get_latest_invoice"]
    result = api_router.call_api(cap)
    assert result["json"]["id"] == "inv_1"


def test_call_api_unavailable_maps_to_api_unavailable(monkeypatch):
    _patch_client(monkeypatch, response=_FakeResponse(503))
    cap = api_router.API_CAPABILITIES["get_latest_invoice"]
    with pytest.raises(api_router.ApiCallError) as exc:
        api_router.call_api(cap)
    assert exc.value.category == "api_unavailable"


def test_call_api_unauthorized_maps_to_authentication_error(monkeypatch):
    _patch_client(monkeypatch, response=_FakeResponse(401))
    cap = api_router.API_CAPABILITIES["get_latest_invoice"]
    with pytest.raises(api_router.ApiCallError) as exc:
        api_router.call_api(cap)
    assert exc.value.category == "authentication_error"


def test_call_api_not_found_maps_to_not_found(monkeypatch):
    _patch_client(monkeypatch, response=_FakeResponse(404))
    cap = api_router.API_CAPABILITIES["get_invoice_by_id"]
    with pytest.raises(api_router.ApiCallError) as exc:
        api_router.call_api(cap, path_params={"invoice_id": "inv_999"})
    assert exc.value.category == "not_found"


def test_call_api_network_error_maps_to_network_error(monkeypatch):
    _patch_client(monkeypatch, raise_exc=httpx.ConnectError("refused"))
    cap = api_router.API_CAPABILITIES["get_latest_invoice"]
    with pytest.raises(api_router.ApiCallError) as exc:
        api_router.call_api(cap)
    assert exc.value.category == "network_error"


def test_call_api_malformed_json_maps_to_extraction_error(monkeypatch):
    _patch_client(monkeypatch, response=_FakeResponse(200, json_data=None))
    cap = api_router.API_CAPABILITIES["get_latest_invoice"]
    with pytest.raises(api_router.ApiCallError) as exc:
        api_router.call_api(cap)
    assert exc.value.category == "extraction_error"
