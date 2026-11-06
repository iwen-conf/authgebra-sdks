"""Real-server SDK contract: native pytest, never a mock PASS. See integration/README.md."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from typing import Any

import pytest

from authgebra import ApiError, AsyncClient, CheckRequest, Client, Config

BASE = os.environ.get("AUTHGEBRA_IT_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="NOT_RUN: AUTHGEBRA_IT_BASE_URL unset")


def credentials() -> tuple[str, str]:
    assert os.environ.get("AUTHGEBRA_IT_CONFIRM_ISOLATED") == "1", "confirm disposable SDK test surface"
    return os.environ["AUTHGEBRA_IT_USER"], os.environ["AUTHGEBRA_IT_PASSWORD"]


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_real_server_contract(asynchronous: bool) -> None:
    asyncio.run(scenario(asynchronous))


async def scenario(asynchronous: bool) -> None:
    username, password = credentials()
    prefix = "it-py-" + uuid.uuid4().hex[:12]
    token = ""
    config = Config(base_url=BASE or "", token_provider=lambda: token)
    client = AsyncClient(config) if asynchronous else Client(config)

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("request_id", "it-py-" + uuid.uuid4().hex)
        if asynchronous:
            assert isinstance(client, AsyncClient)
            return await client.request(method, path, **kwargs)
        assert isinstance(client, Client)
        return client.request(method, path, **kwargs)

    async def error(code: str, status: int, method: str, path: str, **kwargs: Any) -> ApiError:
        with pytest.raises(ApiError) as caught:
            await request(method, path, **kwargs)
        e = caught.value
        assert (e.code, e.status) == (code, status)
        assert e.request_id and e.trace_id
        return e

    try:
        # FLOW-SDK-01: authenticate, then distinguish deny from failure.
        await error("INVALID_CREDENTIALS", 401, "POST", "/api/v1/auth/login",
                    json={"identifier": username, "password": password + "-wrong"})
        login = await request("POST", "/api/v1/auth/login", json={"identifier": username, "password": password})
        assert login.data["status"] == "authenticated" and login.data["access_token"]
        token = login.data["access_token"]
        me = await request("GET", "/api/v1/auth/me")
        subject = me.data["user"]["id"]
        for permission, decision in [("user:read", "allow"), ("nope:nope", "deny")]:
            req = CheckRequest(subject, permission)
            result = await client.authorization.check(req) if isinstance(client, AsyncClient) else client.authorization.check(req)
            assert result.decision == decision
            assert result.meta and result.meta.authz_revision > 0 and result.meta.snapshot_generation
        print("IT-1/2/3 PASS", flush=True)

        # FLOW-SDK-02: own isolated facts, batch partitions and same-generation pages.
        await request("POST", "/api/v1/resource-types", json={"code": prefix, "name": prefix})
        attr = await request("POST", "/api/v1/attributes", json={
            "code": prefix, "name": prefix, "owner_type": "resource", "value_type": "string"})
        secret = "private-" + uuid.uuid4().hex
        refs = [{"resource_type_code": prefix, "external_id": name} for name in ["b", "a"]]
        for ref in refs:
            await request("POST", "/api/v1/resources", json={**ref, "name": ref["external_id"],
                "attributes": [{"attribute_id": attr.data["id"], "value": secret}]})
        missing = {"resource_type_code": prefix, "external_id": "missing"}
        batch = await request("POST", "/api/v1/authz/batch-check", safe=True, json={
            "subject_id": subject, "permission": "user:read", "resources": [refs[0], missing, refs[1], refs[0]]})
        assert [r["resource"]["external_id"] for r in batch.data["allowed"]] == ["b", "a"]
        assert [r["resource"]["external_id"] for r in batch.data["denied"]] == ["missing"]
        body = {"subject_id": subject, "permission": "user:read", "resource_type": prefix, "limit": 1}
        first = await request("POST", "/api/v1/authz/list-resources", safe=True, json=body)
        assert first.page and first.page.has_more and first.page.next_cursor and len(first.data) == 1
        second = await request("POST", "/api/v1/authz/list-resources", safe=True,
                               json={**body, "cursor": first.page.next_cursor})
        assert len(second.data) == 1 and second.data != first.data
        assert second.meta == first.meta and second.page and not second.page.has_more
        cursor = first.page.next_cursor
        bad = ("A" if cursor[0] != "A" else "B") + cursor[1:]
        await error("CURSOR_INVALID", 400, "POST", "/api/v1/authz/list-resources", safe=True,
                    json={**body, "cursor": bad})
        explain = await request("POST", "/api/v1/authz/explain", safe=True, json={
            "subject_id": subject, "permission": "user:read", "resource": refs[0]})
        assert explain.data["decision"] == "allow"
        assert secret not in json.dumps(explain.data)
        revision = await request("GET", "/api/v1/authz/revision")
        assert revision.data["db_revision"] > 0
        assert revision.data["snapshot_generation"] == revision.data["db_revision"]
        assert revision.data["status"] == "in_sync"
        print("IT-4/5/6/7 PASS", flush=True)

        # FLOW-SDK-03: one durable write, replay, conflict, and duplicate contenders.
        user = {"username": prefix, "display_name": prefix, "initial_password": password}
        key = uuid.uuid4().hex
        created = await request("POST", "/api/v1/users", json=user, idempotency_key=key)
        replay = await request("POST", "/api/v1/users", json=user, idempotency_key=key)
        assert created.info.status == 201 and replay.replayed and replay.data["id"] == created.data["id"]
        conflict = await error("IDEMPOTENCY_CONFLICT", 409, "POST", "/api/v1/users",
                               json={**user, "display_name": "different"}, idempotency_key=key)
        assert conflict.retryable is False
        found = await request("GET", "/api/v1/users", query={"q": prefix})
        assert len(found.data) == 1 and found.data[0]["id"] == created.data["id"]
        print("IT-8 PASS", flush=True)

        trace_id = uuid.uuid4().hex
        e = await error("RESOURCE_NOT_FOUND", 404, "GET", "/api/v1/users/" + str(uuid.uuid4()),
                        request_id=prefix + "-missing", traceparent=f"00-{trace_id}-1234567890abcdef-01")
        assert e.trace_id == trace_id and e.request_id == prefix + "-missing"
        for path, query, reason in [("/api/v1/users", {"limit": "abc"}, "invalid_query_parameter"),
                                    ("/api/v1/users/not-a-uuid", None, "invalid_path_parameter")]:
            e = await error("INVALID_REQUEST", 400, "GET", path, query=query)
            assert e.violations and e.violations[0].reason == reason
        e = await error("INVALID_REQUEST", 400, "POST", "/api/v1/auth/login",
                        json={"identifier": username, "password": password, "extra": True})
        assert e.violations and e.violations[0].reason == "unknown_field" and e.violations[0].path
        print("IT-9/10/12 PASS", flush=True)
    finally:
        if isinstance(client, AsyncClient):
            await client.aclose()
        else:
            client.close()


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_real_server_committed_unpublished(asynchronous: bool) -> None:
    if os.environ.get("AUTHGEBRA_IT_FAULT") != "1":
        pytest.skip("NOT_RUN: AUTHGEBRA_IT_FAULT not enabled")
    asyncio.run(committed_unpublished(asynchronous))


async def committed_unpublished(asynchronous: bool) -> None:
    username, password = credentials()
    on, off = os.environ["AUTHGEBRA_IT_FAULT_ON_CMD"], os.environ["AUTHGEBRA_IT_FAULT_OFF_CMD"]
    token = ""
    config = Config(base_url=BASE or "", token_provider=lambda: token)
    client = AsyncClient(config) if asynchronous else Client(config)

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("request_id", "it-py-f-" + uuid.uuid4().hex)
        if isinstance(client, AsyncClient):
            return await client.request(method, path, **kwargs)
        return client.request(method, path, **kwargs)

    try:
        login = await request("POST", "/api/v1/auth/login", json={"identifier": username, "password": password})
        token = login.data["access_token"]
        prefix, key = "it-py-f-" + uuid.uuid4().hex[:12], uuid.uuid4().hex
        body = {"username": prefix, "display_name": prefix, "initial_password": password}
        try:
            subprocess.run(on, shell=True, check=True, timeout=15)
            with pytest.raises(ApiError) as caught:
                await request("POST", "/api/v1/users", json=body, idempotency_key=key)
            e = caught.value
            assert e.code == "AUTHZ_RELOAD_PENDING" and e.status == 503
            assert e.commit_state == "committed_unpublished" and e.retryable is False and e.attempts == 1
        finally:
            subprocess.run(off, shell=True, check=True, timeout=15)
        deadline = time.monotonic() + 60
        while True:
            try:
                rev = await request("GET", "/api/v1/authz/revision")
                if rev.data["db_revision"] == rev.data["snapshot_generation"]:
                    break
            except ApiError as pending:
                assert (pending.code, pending.status) == ("NOT_READY", 503)
            assert time.monotonic() < deadline, "publication failed to recover within 60s"
            # A bounded state poll, not timing-based synchronization.
            await asyncio.sleep(0.2)
        replay = await request("POST", "/api/v1/users", json=body, idempotency_key=key)
        found = await request("GET", "/api/v1/users", query={"q": prefix})
        assert replay.replayed and len(found.data) == 1 and found.data[0]["id"] == replay.data["id"]
        print(f"IT-11 {'async' if asynchronous else 'sync'} PASS", flush=True)
    finally:
        if isinstance(client, AsyncClient):
            await client.aclose()
        else:
            client.close()


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_real_server_inflight(asynchronous: bool) -> None:
    if os.environ.get("AUTHGEBRA_IT_INFLIGHT") != "1":
        pytest.skip("NOT_RUN: first-write database barrier not enabled")
    asyncio.run(inflight(asynchronous))


async def inflight(asynchronous: bool) -> None:
    username, password = credentials()
    token = ""
    config = Config(base_url=BASE or "", token_provider=lambda: token)
    client = AsyncClient(config) if asynchronous else Client(config)

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("request_id", "it-flight-py-" + uuid.uuid4().hex)
        if isinstance(client, AsyncClient):
            return await client.request(method, path, **kwargs)
        return await asyncio.to_thread(client.request, method, path, **kwargs)

    async def control(operation: str) -> None:
        await asyncio.to_thread(subprocess.run, os.environ[f"AUTHGEBRA_IT_INFLIGHT_{operation}_CMD"],
                                shell=True, check=True, timeout=15)

    try:
        login = await request("POST", "/api/v1/auth/login", json={"identifier": username, "password": password})
        token = login.data["access_token"]
        for different in (False, True):
            prefix, key = "it-flight-py-" + uuid.uuid4().hex[:12], uuid.uuid4().hex
            body = {"username": prefix, "display_name": prefix, "initial_password": password}
            await control("HOLD")
            first = asyncio.create_task(request("POST", "/api/v1/users", json=body, idempotency_key=key))
            try:
                await control("WAIT")
                with pytest.raises(ApiError) as caught:
                    await request("POST", "/api/v1/users", json={**body, "display_name": "different"} if different else body,
                                  idempotency_key=key)
            finally:
                await control("RELEASE")
                created = await first
            assert created.info.status == 201
            error = caught.value
            assert (error.status, error.code, error.retryable, error.attempts) == (409, "IDEMPOTENCY_IN_PROGRESS", True, 1)
            replay = await request("POST", "/api/v1/users", json=body, idempotency_key=key)
            found = await request("GET", "/api/v1/users", query={"q": prefix})
            assert replay.replayed and replay.data["id"] == created.data["id"]
            assert len(found.data) == 1 and found.data[0]["id"] == created.data["id"]
            if different:
                with pytest.raises(ApiError) as conflict:
                    await request("POST", "/api/v1/users", json={**body, "display_name": "different"}, idempotency_key=key)
                assert (conflict.value.status, conflict.value.code, conflict.value.retryable) == (409, "IDEMPOTENCY_CONFLICT", False)
        print(f"first-write inflight {'async' if asynchronous else 'sync'} PASS", flush=True)
    finally:
        if isinstance(client, AsyncClient):
            await client.aclose()
        else:
            client.close()
