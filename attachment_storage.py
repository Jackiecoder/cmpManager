"""Private, immutable attachment objects; business authorization lives in app.py."""
import hashlib
from pathlib import Path

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage
from google.cloud.storage.retry import DEFAULT_RETRY

MAX_BYTES = 20 * 1024 * 1024
RETRY = DEFAULT_RETRY.with_deadline(60)


class ObjectConflict(Exception):
    pass


def object_name(workspace, record_id):
    return 'attachments/' + hashlib.sha256(workspace.encode()).hexdigest() + '/' + record_id


def content_type(filename, content):
    # Never use a caller-supplied MIME type to render active content on our origin.
    if content.startswith(b'%PDF-'): return 'application/pdf'
    if content.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if content.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if content.startswith((b'GIF87a', b'GIF89a')): return 'image/gif'
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP': return 'image/webp'
    if Path(filename).suffix.lower() in ('.txt', '.csv', '.md', '.log', '.json') and b'\x00' not in content:
        try: content.decode('utf-8-sig'); return 'text/plain'
        except UnicodeDecodeError: pass
    return 'application/octet-stream'


def store(bucket_name, name, content, mime, digest):
    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(name)
    blob.metadata = {'sha256': digest}
    blob.cache_control = 'private, no-store'
    try:
        # A retry can reconcile the object even if the database commit was lost.
        blob.upload_from_string(content, content_type=mime, if_generation_match=0, timeout=60, retry=RETRY)
    except PreconditionFailed:
        blob = bucket.get_blob(name, timeout=30, retry=RETRY)
        if not blob or (blob.metadata or {}).get('sha256') != digest or int(blob.size) != len(content):
            raise ObjectConflict('Existing attachment differs') from None
    return {'storage_provider': 'gcs', 'storage_bucket': bucket_name, 'storage_object': name,
            'storage_generation': str(blob.generation), 'upload_sha256': digest,
            'mime_type': mime, 'size': len(content)}


def read(info):
    generation = int(info['storage_generation'])
    blob = storage.Client().bucket(info['storage_bucket']).blob(info['storage_object'], generation=generation)
    content = blob.download_as_bytes(start=0, end=MAX_BYTES, if_generation_match=generation, timeout=60, retry=RETRY)
    if len(content) > MAX_BYTES or len(content) != info['size'] or hashlib.sha256(content).hexdigest() != info['upload_sha256']:
        raise ObjectConflict('Attachment integrity check failed')
    return content
