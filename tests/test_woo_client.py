import httpx
import pytest

from app import extractor
from app.extractor import WooClient


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    esperas = []
    monkeypatch.setattr(extractor.time, "sleep", lambda s: esperas.append(s))
    extractor._throttles.clear()
    return esperas


def _client(respuestas):
    it = iter(respuestas)

    def handler(request):
        return next(it)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_429_se_reintenta_y_respeta_retry_after(sin_esperas):
    c = _client([httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json=[{"sku": "A"}])])
    woo = WooClient("https://tienda.test", client=c)
    assert woo.buscar("A") == [{"sku": "A"}]
    assert 7.0 in sin_esperas


def test_429_persistente_da_mensaje_claro():
    c = _client([httpx.Response(429)] * 3)
    woo = WooClient("https://tienda.test", client=c, retries=3)
    with pytest.raises(RuntimeError, match="limitando las peticiones"):
        woo.buscar("A")


def test_404_no_se_reintenta():
    c = _client([httpx.Response(404)])
    woo = WooClient("https://tienda.test", client=c)
    with pytest.raises(httpx.HTTPStatusError):
        woo.buscar("A")


def test_throttle_es_compartido_por_host():
    a = extractor.throttle_para("https://tienda.test/x", 1.0)
    b = extractor.throttle_para("https://TIENDA.test/y", 1.0)
    assert a is b
