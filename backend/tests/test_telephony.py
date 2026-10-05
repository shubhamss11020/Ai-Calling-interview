import xml.etree.ElementTree as ET

import pytest

from app.core.config import settings
from app.services import telephony_service as tel


def _parse(xml: str):
    return ET.fromstring(xml)  # raises if not well-formed


def test_webhook_tokens_bind_to_interview_id():
    t = tel.sign_interview("abc")
    assert tel.verify_interview_token("abc", t)
    assert not tel.verify_interview_token("abd", t)
    assert not tel.verify_interview_token("abc", None)
    assert not tel.verify_interview_token("abc", "x" * 24)


def test_webhook_url_is_absolute_and_signed(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://abc.ngrok.app/")
    url = tel.webhook_url("speech", "int-1", silent=1)
    assert url.startswith("https://abc.ngrok.app/api/v1/interviews/telephony/vobiz/speech?")
    assert "interview_id=int-1" in url and "token=" in url and "silent=1" in url


def test_public_url_problems(monkeypatch):
    for bad in ("http://localhost:8000", "https://your-api.example.com", "http://abc.ngrok.app"):
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", bad)
        assert tel.public_url_problem()
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://abc.ngrok.app")
    assert tel.public_url_problem() is None


def test_gather_pin_xml_is_valid_and_escaped():
    root = _parse(tel.xml_gather_pin("Enter your PIN & press #", "https://x.app/a?interview_id=1&token=2"))
    gather = root.find("Gather")
    assert gather.attrib["inputType"] == "dtmf" and gather.attrib["numDigits"] == "6"
    assert gather.attrib["action"] == "https://x.app/a?interview_id=1&token=2"
    assert "&" in gather.find("Speak").text
    assert root.find("Hangup") is not None  # silence falls through to a polite hangup


def test_gather_speech_xml_has_retry_redirect_and_hints():
    root = _parse(tel.xml_gather_speech("Tell me about <Redis>", "https://x.app/s", "https://x.app/s?silent=1", ["Python", "Redis"]))
    g = root.find("Gather")
    assert g.attrib["inputType"] == "speech" and g.attrib["hints"] == "Python,Redis"
    assert g.find("Speak").text == "Tell me about <Redis>"
    assert root.find("Redirect").text == "https://x.app/s?silent=1"


def test_say_and_hangup_xml():
    root = _parse(tel.xml_say_and_hangup("Goodbye"))
    assert root.find("Speak").text == "Goodbye" and root.find("Hangup") is not None


@pytest.mark.asyncio
async def test_outbound_call_refused_when_public_url_is_not_public(monkeypatch):
    monkeypatch.setattr(settings, "VOBIZ_AUTH_ID", "MA_X")
    monkeypatch.setattr(settings, "VOBIZ_AUTH_TOKEN", "t")
    monkeypatch.setattr(settings, "VOBIZ_PHONE_NUMBER", "+910000000000")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")
    res = await tel.vobiz_service.initiate_outbound_call("+919999999999", "i-1", "123456")
    assert res["status"] == "ERROR" and "PUBLIC_BASE_URL" in res["error"]


@pytest.mark.asyncio
async def test_outbound_call_payload_matches_vobiz_api(monkeypatch):
    monkeypatch.setattr(settings, "VOBIZ_AUTH_ID", "MA_X")
    monkeypatch.setattr(settings, "VOBIZ_AUTH_TOKEN", "secret")
    monkeypatch.setattr(settings, "VOBIZ_PHONE_NUMBER", "+910000000000")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://abc.ngrok.app")
    seen = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"request_uuid": "uuid-123", "message": "Call fired"}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, json=None, headers=None):
            seen.update(url=url, json=json, headers=headers)
            return FakeResp()

    monkeypatch.setattr(tel.httpx, "AsyncClient", FakeClient)
    res = await tel.vobiz_service.initiate_outbound_call("+919999999999", "i-1", "123456")
    assert res == {"status": "PLACED", "call_id": "uuid-123"}
    assert seen["url"] == "https://api.vobiz.ai/api/v1/Account/MA_X/Call/"
    assert seen["headers"]["X-Auth-ID"] == "MA_X" and seen["headers"]["X-Auth-Token"] == "secret"
    assert seen["json"]["from"] == "+910000000000" and seen["json"]["to"] == "+919999999999"
    assert seen["json"]["answer_url"].startswith("https://abc.ngrok.app/") and "token=" in seen["json"]["answer_url"]
    assert "hangup_url" in seen["json"]


def test_stream_xml_and_ws_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://abc.ngrok.app")
    url = tel.ws_stream_url("int-9")
    assert url == f"wss://abc.ngrok.app/ws/vobiz/int-9/{tel.sign_interview('int-9')}"
    root = _parse(tel.xml_stream(url, "Connecting you now."))
    stream = root.find("Stream")
    assert stream.attrib["bidirectional"] == "true" and stream.attrib["keepCallAlive"] == "true"
    assert stream.attrib["contentType"] == "audio/x-l16;rate=16000" and stream.text == url
    assert root.find("Speak").text == "Connecting you now." and root.find("Hangup") is not None
