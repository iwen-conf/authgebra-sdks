"""Real sockets and certificates; run through integration/tls.py."""
import asyncio
import os
import ssl

import pytest

from authgebra import ApiResult, AsyncClient, Client, Config, RetryPolicy, TransportError


@pytest.mark.skipif(not os.environ.get("SDK_TLS_VALID_URL"), reason="NOT_RUN: TLS fixture unset")
def test_real_tls_verification() -> None:
    async def scenario() -> None:
        context = ssl.create_default_context(cafile=os.environ["SDK_TLS_CA"])
        for asynchronous in (False, True):
            for origin, trust, succeeds in (
                ("SDK_TLS_VALID_URL", True, False),
                ("SDK_TLS_MISMATCH_URL", context, False),
                ("SDK_TLS_PLAIN_URL", context, False),
                ("SDK_TLS_VALID_URL", context, True),
            ):
                config = Config(base_url=os.environ[origin], verify=trust, retry=RetryPolicy(max_retries=0))
                client = AsyncClient(config) if asynchronous else Client(config)
                try:
                    async def request() -> ApiResult:
                        if isinstance(client, AsyncClient):
                            return await client.request("GET", "/tls-test")
                        return client.request("GET", "/tls-test")
                    if succeeds:
                        result = await request()
                        assert result.data["tls"] is True
                    else:
                        with pytest.raises(TransportError) as caught:
                            await request()
                        assert caught.value.error == "tls_error"
                        assert caught.value.attempts == 1
                finally:
                    if isinstance(client, AsyncClient):
                        await client.aclose()
                    else:
                        client.close()
    asyncio.run(scenario())
