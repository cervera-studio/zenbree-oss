"""The R2 bucket for user uploads, through its S3 API: four signed (SigV4) requests (put, get, list, delete), no SDK."""

import hashlib
import hmac
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import NamedTuple
from urllib.parse import quote

import httpx

from app.core.config import settings


class Stored(NamedTuple):
    key: str
    modified: datetime


class ObjectStoreError(Exception):
    pass


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


class R2:
    def __init__(self, endpoint: str, key_id: str, secret: str, bucket: str) -> None:
        self.endpoint, self.key_id, self.secret, self.bucket = endpoint.rstrip('/'), key_id, secret, bucket

    def _sign(
        self, method: str, path: str, payload: bytes, content_type: str | None, query: str = ''
    ) -> dict[str, str]:
        now = datetime.now(UTC)
        stamp, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
        host = self.endpoint.split('://', 1)[1]
        digest = hashlib.sha256(payload).hexdigest()
        headers = {'host': host, 'x-amz-content-sha256': digest, 'x-amz-date': stamp}
        if content_type:
            headers['content-type'] = content_type
        names = ';'.join(sorted(headers))
        canonical = '\n'.join([method, path, query, *(f'{k}:{headers[k]}' for k in sorted(headers)), '', names, digest])
        scope = f'{day}/auto/s3/aws4_request'
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key = _hmac(_hmac(_hmac(_hmac(f'AWS4{self.secret}'.encode(), day), 'auto'), 's3'), 'aws4_request')
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers['authorization'] = (
            f'AWS4-HMAC-SHA256 Credential={self.key_id}/{scope}, SignedHeaders={names}, Signature={signature}'
        )
        return headers

    async def _call(
        self, method: str, key: str, body: bytes = b'', content_type: str | None = None, query: str = ''
    ) -> httpx.Response:
        path = f'/{self.bucket}/{quote(key)}' if key else f'/{self.bucket}'
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.request(
                    method,
                    self.endpoint + path + (f'?{query}' if query else ''),
                    content=body,
                    headers=self._sign(method, path, body, content_type, query),
                )
        except httpx.HTTPError as exc:
            raise ObjectStoreError(type(exc).__name__) from exc
        if res.status_code >= 500 or res.status_code in (401, 403):
            raise ObjectStoreError(f'{method} {res.status_code}')
        return res

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        res = await self._call('PUT', key, data, content_type)
        if res.status_code >= 300:
            raise ObjectStoreError(f'PUT {res.status_code}')

    async def get(self, key: str) -> bytes | None:
        res = await self._call('GET', key)
        return res.content if res.status_code == 200 else None

    async def list(self, prefix: str) -> list[Stored]:
        """Every object under ``prefix``, following the continuation token past the 1000 a page holds."""
        found: list[Stored] = []
        token = ''
        while True:
            # the canonical query is sorted by name: continuation-token, list-type, prefix
            query = (f'continuation-token={quote(token, safe="")}&' if token else '') + (
                f'list-type=2&prefix={quote(prefix, safe="")}'
            )
            res = await self._call('GET', '', query=query)
            if res.status_code != 200:
                raise ObjectStoreError(f'LIST {res.status_code}')
            root = ET.fromstring(res.content)  # noqa: S314 - our own bucket, over TLS
            fields = {e.tag.rsplit('}', 1)[-1]: e for e in root}
            for item in (e for e in root if e.tag.endswith('Contents')):
                kv = {c.tag.rsplit('}', 1)[-1]: c.text or '' for c in item}
                found.append(Stored(kv['Key'], datetime.fromisoformat(kv['LastModified'])))
            more = fields.get('NextContinuationToken')
            if fields['IsTruncated'].text != 'true' or more is None:
                return found
            token = more.text or ''

    async def delete(self, key: str) -> None:
        await self._call('DELETE', key)


def photo_store() -> R2 | None:
    """The photos bucket, or None when it is not configured (the feature is inert)."""
    s = settings
    if not (s.r2_endpoint and s.r2_access_key_id and s.r2_secret_access_key and s.r2_photos_bucket):
        return None
    return R2(s.r2_endpoint, s.r2_access_key_id, s.r2_secret_access_key, s.r2_photos_bucket)
