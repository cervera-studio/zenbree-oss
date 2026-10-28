"""#503: a user's profile photo, with a dict standing in for the R2 bucket."""

import io
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.auth import photo
from app.auth.middleware import CurrentUser
from app.core.config import settings
from app.core.object_store import R2, Stored
from app.main import app

OLDER = datetime(2026, 1, 1, tzinfo=UTC)
ADA = CurrentUser(id='11111111-1111-1111-1111-111111111111', org_id='o1', org_role='admin')
BOB = CurrentUser(id='22222222-2222-2222-2222-222222222222', org_id='o1', org_role='admin')


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.modified: dict[str, datetime] = {}

    async def put(self, key, data, content_type):
        assert content_type == 'image/webp'
        self.objects[key] = data
        self.modified[key] = datetime.now(UTC) + timedelta(microseconds=len(self.objects))

    async def get(self, key):
        return self.objects.get(key)

    async def list(self, prefix):
        return [Stored(k, self.modified.get(k, datetime.now(UTC))) for k in self.objects if k.startswith(prefix)]

    async def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def store(monkeypatch, sign_in_without_db):
    fake = FakeStore()
    monkeypatch.setattr(photo, 'photo_store', lambda: fake)
    monkeypatch.setattr(settings, 'api_public_base_url', 'https://api.example.com')
    yield fake
    app.dependency_overrides.clear()


def _image(fmt='PNG', size=(600, 300), color='red') -> bytes:
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, fmt)
    return out.getvalue()


def _put(user, data, sign_in):
    sign_in(user)
    return TestClient(app).put('/v1/me/photo', content=data, headers={'Content-Type': 'application/octet-stream'})


@pytest.mark.parametrize('fmt', ['PNG', 'JPEG', 'WEBP'])
def test_an_image_becomes_a_square_webp_under_the_callers_key(store, sign_in_without_db, fmt):
    res = _put(ADA, _image(fmt), sign_in_without_db)
    assert res.status_code == 200
    assert res.json()['url'].startswith(f'https://api.example.com/v1/avatars/{ADA.id}/')
    (key,) = store.objects
    assert key.startswith(f'avatars/{ADA.id}/')
    stored = Image.open(io.BytesIO(store.objects[key]))
    assert (stored.format, stored.size) == ('WEBP', (256, 256))


@pytest.mark.parametrize(
    ('data', 'status', 'code'),
    [
        (b'not an image', 422, 'PHOTO.BAD_TYPE'),
        (_image('GIF'), 422, 'PHOTO.BAD_TYPE'),
        (b'x' * (5 * 1024 * 1024 + 1), 413, 'REQUEST.TOO_LARGE'),
    ],
)
def test_a_bad_upload_is_refused_and_stores_nothing(store, sign_in_without_db, data, status, code):
    res = _put(ADA, data, sign_in_without_db)
    assert (res.status_code, res.json()['detail']['code']) == (status, code)
    assert store.objects == {}


def _path(res):
    return res.json()['url'].removeprefix('https://api.example.com')


def test_replace_and_remove_delete_the_old_object_so_its_url_answers_404(store, sign_in_without_db):
    old = _path(_put(ADA, _image(), sign_in_without_db))
    bob = _path(_put(BOB, _image(), sign_in_without_db))
    new = _path(_put(ADA, _image(color='blue'), sign_in_without_db))
    assert new != old
    app.dependency_overrides.clear()
    assert TestClient(app).get(old).status_code == 404
    assert TestClient(app).get(new).status_code == 200
    sign_in_without_db(ADA)
    assert TestClient(app).delete('/v1/me/photo').status_code == 204
    app.dependency_overrides.clear()
    assert TestClient(app).get(new).json()['detail']['code'] == 'PHOTO.NOT_FOUND'
    assert TestClient(app).get(bob).status_code == 200  # another user's photo is untouched


def test_the_photo_is_cached_by_the_browser_only(store, sign_in_without_db):
    url = _path(_put(ADA, _image(), sign_in_without_db))
    app.dependency_overrides.clear()
    got = TestClient(app).get(url)
    assert (got.status_code, got.headers['content-type']) == (200, 'image/webp')
    assert got.headers['cache-control'] == 'private, max-age=86400'
    assert TestClient(app).get(f'/v1/avatars/{ADA.id}/short').status_code == 404


def test_unconfigured_is_unavailable(monkeypatch, sign_in_without_db):
    monkeypatch.setattr(photo, 'photo_store', lambda: None)
    res = _put(ADA, _image(), sign_in_without_db)
    assert (res.status_code, res.json()['detail']['code']) == (503, 'PHOTO.UNAVAILABLE')
    app.dependency_overrides.clear()


def test_the_request_is_signed_like_s3_expects():
    from app.core.object_store import R2

    h = R2('https://acc.r2.cloudflarestorage.com', 'AKID', 'secret', 'b')._sign('PUT', '/b/k', b'x', 'image/webp')
    assert h['authorization'].startswith('AWS4-HMAC-SHA256 Credential=AKID/')
    assert 'SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date,' in h['authorization']


async def test_list_follows_the_continuation_token(monkeypatch):
    pages = {
        '': ('true', 'tok/1', [('a', '2026-10-01T10:00:00.000Z')]),
        'tok/1': ('false', None, [('b', '2026-10-02T10:00:00.000Z')]),
    }
    seen = []

    class Res:
        status_code = 200

        def __init__(self, content):
            self.content = content

    store = R2('https://acc.r2.cloudflarestorage.com', 'AK', 's', 'b')

    async def call(method, key, body=b'', content_type=None, query=''):
        seen.append(query)
        token = (
            query.split('continuation-token=')[1].split('&')[0].replace('%2F', '/') if 'continuation' in query else ''
        )
        more, nxt, items = pages[token]
        contents = ''.join(f'<Contents><Key>{k}</Key><LastModified>{m}</LastModified></Contents>' for k, m in items)
        nxt_xml = f'<NextContinuationToken>{nxt}</NextContinuationToken>' if nxt else ''
        return Res(
            f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>{more}</IsTruncated>'
            f'{nxt_xml}{contents}</ListBucketResult>'.encode()
        )

    monkeypatch.setattr(store, '_call', call)
    assert [s.key for s in await store.list('avatars/')] == ['a', 'b']
    assert seen[1] == 'continuation-token=tok%2F1&list-type=2&prefix=avatars%2F'


async def test_an_upload_never_deletes_a_newer_object(store):
    store.objects = {f'avatars/{ADA.id}/older': b'x', f'avatars/{ADA.id}/newer': b'x'}
    store.modified = {f'avatars/{ADA.id}/older': OLDER, f'avatars/{ADA.id}/newer': OLDER + timedelta(seconds=1)}
    await photo._delete_all(store, ADA.id, keep=f'avatars/{ADA.id}/older')
    assert sorted(store.objects) == [f'avatars/{ADA.id}/newer', f'avatars/{ADA.id}/older']
    await photo._delete_all(store, ADA.id, keep=f'avatars/{ADA.id}/newer')
    assert list(store.objects) == [f'avatars/{ADA.id}/newer']


def test_the_photo_is_served_with_nosniff(store, sign_in_without_db):
    url = _path(_put(ADA, _image(), sign_in_without_db))
    app.dependency_overrides.clear()
    assert TestClient(app).get(url).headers['x-content-type-options'] == 'nosniff'


def test_a_png_over_the_pixel_cap_is_too_large(store, sign_in_without_db):
    res = _put(ADA, _image(size=(5200, 5000)), sign_in_without_db)  # 26 Mpx
    assert (res.status_code, res.json()['detail']['code']) == (422, 'PHOTO.TOO_LARGE')


@pytest.mark.parametrize(('fmt', 'side', 'limit_mb'), [('PNG', 5000, 100), ('WEBP', 2800, 150)])
def test_a_big_upload_stays_within_a_bounded_memory(fmt, side, limit_mb):
    """The largest image each format accepts (25 Mpx PNG, 8 Mpx WebP): the cut right after the decode keeps the peak
    near the decode alone (a 25 Mpx PNG measured 200 MB without it). A fresh process, whose peak RSS is its own."""
    code = (
        'import io, resource, sys\nfrom PIL import Image\nfrom app.auth.photo import _square\n'
        f'b = io.BytesIO(); Image.new("RGB", ({side}, {side}), "red").save(b, "{fmt}"); data = b.getvalue()\n'
        'before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n_square(data)\n'
        'print((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) * (1 if sys.platform == "darwin" else 1024))'
    )
    out = subprocess.run(  # noqa: S603 - our own interpreter and a literal script
        [sys.executable, '-c', code], capture_output=True, text=True, check=True
    )
    assert float(out.stdout) < limit_mb * 1024 * 1024


def test_a_webp_over_its_pixel_cap_is_too_large(store, sign_in_without_db):
    res = _put(ADA, _image('WEBP', size=(3000, 3000)), sign_in_without_db)  # 9 Mpx
    assert (res.status_code, res.json()['detail']['code']) == (422, 'PHOTO.TOO_LARGE')
