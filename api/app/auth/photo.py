"""A user's own profile photo (#503). It belongs to the user, not an organization: the object keys sit under the
caller's id, so no route can reach another user's photo, and no database row exists. The photo is served by the API (an
``<img>`` cannot send a token), cached by the browser only."""

import asyncio
import io
import re
import secrets
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, current_user, public
from app.core.config import settings
from app.core.errors_http import api_error
from app.core.object_store import ObjectStoreError, photo_store

router = APIRouter(tags=['account'])
logger = structlog.get_logger()

MAX_BYTES = 5 * 1024 * 1024
# A JPEG is decoded at a fraction of its size (draft); PNG and WebP decode whole, at 4 bytes a pixel, so they get less.
MAX_JPEG_PIXELS = 40_000_000
MAX_PIXELS = 25_000_000
# WebP has no reduced-size decode (no draft), and its decoder takes about 17 bytes a pixel: 8 Mpx is about 135 MB.
MAX_WEBP_PIXELS = 8_000_000
_PIXEL_CAP = {'JPEG': MAX_JPEG_PIXELS, 'PNG': MAX_PIXELS, 'WEBP': MAX_WEBP_PIXELS}
_DECODING = asyncio.Semaphore(2)  # process-wide: two decodes at once bound the memory a burst of uploads can take
SIZE = 256
FORMATS = {'JPEG', 'PNG', 'WEBP'}


class PhotoUrl(BaseModel):
    url: str


_TOKEN = re.compile(r'[A-Za-z0-9_-]{16,64}')


def _prefix(user_id: str) -> str:
    return f'avatars/{user_id}/'


async def _delete_all(store, user_id: str, keep: str | None = None) -> None:
    """Every photo object of the user, or with ``keep``, those written before it: an old URL must stop answering, and
    two uploads at once must not delete each other's object (the older one goes, the newer stays)."""
    stored = await store.list(_prefix(user_id))
    newest = next((s.modified for s in stored if s.key == keep), None)
    for s in stored:
        if keep is None or (newest is not None and s.modified < newest):
            await store.delete(s.key)


def _unavailable() -> Exception:
    return api_error(503, 'PHOTO.UNAVAILABLE', 'Profile photos are unavailable right now')


def _bad_type() -> Exception:
    return api_error(422, 'PHOTO.BAD_TYPE', 'The photo must be a JPEG, PNG or WebP image')


def _too_large() -> Exception:
    return api_error(422, 'PHOTO.TOO_LARGE', 'The photo has too many pixels')


def _square(data: bytes) -> bytes:
    """The upload as a 256 px square WebP. The bytes are decoded, so the content type the client sent is never used.
    The size is cut right after the decode, before the colour conversion and the rotation copy the pixels again."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in FORMATS:
                raise _bad_type()
            if image.width * image.height > _PIXEL_CAP[image.format]:
                raise _too_large()
            if image.format == 'JPEG':
                image.draft('RGB', (SIZE * 2, SIZE * 2))  # the decoder scales by 1/2, 1/4 or 1/8 while it reads
            image.thumbnail((SIZE * 2, SIZE * 2))
            square = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGBA'), (SIZE, SIZE))
    except Image.DecompressionBombError:
        raise _too_large() from None
    except UnidentifiedImageError, OSError:
        raise _bad_type() from None
    out = io.BytesIO()
    square.save(out, 'WEBP', quality=85)
    return out.getvalue()


@router.put('/v1/me/photo', response_model=PhotoUrl)
async def put_photo(request: Request, user: CurrentUser = Depends(current_user)) -> PhotoUrl:
    """The body is the image itself, any content type. Replaces the caller's photo and returns its new URL."""
    store = photo_store()
    if store is None or not settings.api_public_base_url:
        raise _unavailable()
    data = await request.body()  # capped at MAX_BYTES by BodyLimit (app/main.py): 413 REQUEST.TOO_LARGE
    async with _DECODING:
        webp = await asyncio.to_thread(_square, data)
    try:
        token = secrets.token_urlsafe(16)
        key = f'{_prefix(user.id)}{token}.webp'
        await store.put(key, webp, 'image/webp')
        await _delete_all(store, user.id, keep=key)
    except ObjectStoreError as exc:
        logger.error('photo.put_failed', error=str(exc))
        raise _unavailable() from None
    base = settings.api_public_base_url.rstrip('/')
    return PhotoUrl(url=f'{base}/v1/avatars/{user.id}/{token}')


@router.delete('/v1/me/photo', status_code=204)
async def delete_photo(user: CurrentUser = Depends(current_user)) -> Response:
    store = photo_store()
    if store is None:
        raise _unavailable()
    try:
        await _delete_all(store, user.id)
    except ObjectStoreError as exc:
        logger.error('photo.delete_failed', error=str(exc))
        raise _unavailable() from None
    return Response(status_code=204)


@router.get(
    '/v1/avatars/{user_id}/{token}',
    dependencies=[public(None)],
    response_class=Response,
    responses={200: {'content': {'image/webp': {'schema': {'type': 'string', 'format': 'binary'}}}}},
)
async def get_photo(user_id: UUID, token: str) -> Response:
    """Public by design, like the provider photos it replaces. The random ``token`` of the upload makes the URL
    unguessable, not the user id, which org members can see. A replace or remove deletes the object, so the old URL
    answers 404; the browser may keep it for a day, shared caches never."""
    store = photo_store()
    if store is None or not _TOKEN.fullmatch(token):
        raise api_error(404, 'PHOTO.NOT_FOUND', 'No photo')
    try:
        data = await store.get(f'{_prefix(str(user_id))}{token}.webp')
    except ObjectStoreError as exc:
        logger.error('photo.get_failed', error=str(exc))
        raise _unavailable() from None
    if data is None:
        raise api_error(404, 'PHOTO.NOT_FOUND', 'No photo')
    return Response(
        data,
        media_type='image/webp',
        headers={'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'},
    )


async def remove_photo(user_id: str) -> None:
    """Best effort, for the account delete: a bucket that is down must not stop the delete."""
    store = photo_store()
    if store is None:
        return
    try:
        await _delete_all(store, user_id)
    except ObjectStoreError as exc:
        logger.warning('photo.remove_failed', error=str(exc))
