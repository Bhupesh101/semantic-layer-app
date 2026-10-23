"""End to end through the API with a fake LLM: provider -> upload -> generate -> review -> approve -> chat."""

import copy
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from .conftest import orders_model_dict


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


class FakeLLM:
    """Generation: first an invalid draft (to exercise repair), then a valid one."""

    def __init__(self):
        self.calls = []

    def __call__(self, db, provider, model, messages, purpose):
        self.calls.append(messages)
        data = orders_model_dict()
        if len(self.calls) == 1:
            bad = copy.deepcopy(data)
            bad["metrics"][0]["expression"] = "SUM(revenue_amount)"  # unknown column
            return "```json\n" + json.dumps(bad) + "\n```"
        return json.dumps(data)


def _chunk(content=None, tool_calls=None, usage=None):
    choices = [] if usage else [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return SimpleNamespace(choices=choices, usage=usage)


class FakeAsyncClient:
    def __init__(self):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.requests.append(copy.deepcopy(kw))
        n = len(self.requests)

        async def stream():
            if n == 1:
                args = json.dumps({"metrics": ["total_revenue"], "dimensions": ["city"], "limit": 2})
                tc = SimpleNamespace(
                    index=0, id="call_1", function=SimpleNamespace(name="query_metrics", arguments=args)
                )
                yield _chunk(tool_calls=[tc])
            else:
                for word in ["Mumbai ", "leads."]:
                    yield _chunk(content=word)
            yield _chunk(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10))

        return stream()


def _events(body: str):
    out = []
    for frame in body.strip().split("\n\n"):
        data = next((ln[6:] for ln in frame.splitlines() if ln.startswith("data: ")), None)
        if data:
            out.append(json.loads(data))
    return out


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_full_flow(client, orders_csv, monkeypatch):
    # P1: provider with a masked, encrypted key
    r = client.post(
        "/api/providers",
        json={
            "name": "OR",
            "type": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or-v1-secretsecret1234",
            "default_model": "openai/gpt-4o-mini",
        },
    )
    assert r.status_code == 201, r.text
    prov = r.json()
    assert prov["is_default"] and prov["api_key_masked"] == "sk-…1234" and "secret" not in r.text
    assert client.post("/api/providers", json={"name": "x", "type": "openrouter", "base_url": "u"}).status_code == 422

    # P2: upload, preview, ingest
    with open(orders_csv, "rb") as f:
        r = client.post("/api/sources/upload", files={"file": ("orders.csv", f, "text/csv")})
    assert r.status_code == 201, r.text
    up = r.json()
    sid = up["source"]["id"]
    assert len(up["preview"]["rows"]) == 50 and up["preview"]["options"]["delimiter"] == ","
    r = client.post(
        f"/api/sources/{sid}/ingest",
        json={"name": "Orders", "description": "Test shop orders", "options": up["preview"]["options"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "profiled" and r.json()["row_count"] == 300

    # P3: payload preview never includes raw PII
    r = client.post(f"/api/sources/{sid}/semantic/payload-preview", json={})
    sent = json.dumps(r.json()["messages"])
    assert "user1@example.com" not in sent and "***@example.com" in sent
    strict = json.dumps(
        client.post(f"/api/sources/{sid}/semantic/payload-preview", json={"strict_privacy": True}).json()["messages"]
    )
    assert "Mumbai" not in strict

    fake = FakeLLM()
    monkeypatch.setattr("app.semantic.generator._complete_json", fake)
    r = client.post(f"/api/sources/{sid}/semantic/generate", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("pending", "running"):
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded", job
    assert len(fake.calls) == 2, "expected one repair round"
    assert "revenue_amount" in fake.calls[1][-1]["content"]
    vid = job["result_version_id"]

    # P4: review, override, provenance, diff baseline
    v = client.get(f"/api/semantic/{vid}").json()
    assert v["status"] == "draft" and not [i for i in v["issues"] if i["severity"] == "error"]
    model = v["model"]
    city = next(c for c in model["columns"] if c["name"] == "city")
    city["description"] = "City the order was shipped to"
    r = client.put(f"/api/semantic/{vid}", json={"model": model})
    assert r.status_code == 200
    saved = r.json()
    city = next(c for c in saved["model"]["columns"] if c["name"] == "city")
    assert city["provenance"] == {"description": "user_override"} and saved["origin"] == "ai+user"
    assert next(c for c in saved["base"]["columns"] if c["name"] == "city")["description"] == "Delivery city"

    r = client.post(f"/api/semantic/{vid}/test-metric", json={"expression": "SUM(amount)", "filters": []})
    assert r.json()["ok"] and r.json()["value"] > 0
    assert not client.post(f"/api/semantic/{vid}/test-metric", json={"expression": "amount"}).json()["ok"]

    # approval is blocked by errors
    broken = copy.deepcopy(saved["model"])
    broken["metrics"][0]["expression"] = "SUM(nope)"
    client.put(f"/api/semantic/{vid}", json={"model": broken})
    r = client.post(f"/api/semantic/{vid}/approve")
    assert r.status_code == 422
    client.put(f"/api/semantic/{vid}", json={"model": saved["model"]})
    r = client.post(f"/api/semantic/{vid}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["source"]["status"] == "onboarded"

    # P5: export/import round trip and versioning
    yml = client.get(f"/api/semantic/{vid}/export").text
    assert "total_revenue" in yml and "provenance" not in yml
    r = client.post(f"/api/sources/{sid}/semantic/draft")
    v2 = r.json()
    assert v2["version"] == 2 and v2["status"] == "draft"
    assert client.post(f"/api/sources/{sid}/semantic/draft").json()["id"] == v2["id"]

    # P7: chat with a fake streaming tool-calling model
    fake_chat = FakeAsyncClient()
    monkeypatch.setattr("app.chat.orchestrator.async_client", lambda p: fake_chat)
    r = client.post("/api/chat/sessions", json={"source_id": sid})
    assert r.status_code == 201, r.text
    chat_id = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{chat_id}/messages", json={"content": "Top 2 cities by revenue?"})
    events = _events(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "user_message" and types[-1] == "done"
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["ok"] and len(tool_result["result"]["rows"]) == 2 and "sql" in tool_result["result"]
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Mumbai leads."
    second = fake_chat.requests[1]["messages"]
    assert second[0]["role"] == "system" and "SEMANTIC MODEL" in second[0]["content"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    assert "customer_email" not in json.dumps(second[-1])

    s = client.get(f"/api/chat/sessions/{chat_id}").json()
    assert [m["role"] for m in s["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert s["title"].startswith("Top 2 cities")
    md = client.get(f"/api/chat/sessions/{chat_id}/export").text
    assert "Mumbai leads." in md and "```sql" in md

    # retry replays the last question
    fake_chat.requests.clear()
    r = client.post(f"/api/chat/sessions/{chat_id}/retry")
    assert _events(r.text)[-1]["type"] == "done"
    assert len(client.get(f"/api/chat/sessions/{chat_id}").json()["messages"]) == 4

    # usage log captured the calls
    usage = client.get("/api/usage").json()
    assert any(c["purpose"] == "chat" and c["tokens_in"] == 100 for c in usage["calls"])

    # P5-07 delete removes files
    r = client.delete(f"/api/sources/{sid}")
    assert r.status_code == 200
    assert client.get(f"/api/sources/{sid}").status_code == 404


def test_upload_rejects_non_csv(client):
    r = client.post("/api/sources/upload", files={"file": ("x.xlsx", b"PK..", "application/octet-stream")})
    assert r.status_code == 415


def test_chat_requires_onboarded_source(client, orders_csv):
    with open(orders_csv, "rb") as f:
        sid = client.post("/api/sources/upload", files={"file": ("o.csv", f, "text/csv")}).json()["source"]["id"]
    assert client.post("/api/chat/sessions", json={"source_id": sid}).status_code == 409
