"""A GCS fake preserving create-only generations and byte integrity semantics."""
from google.api_core.exceptions import NotFound, PreconditionFailed


class CloudStore:
    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.reads = 0
        self.on_upload = None
        self.on_read = None

    def bucket(self, name):
        return Bucket(self, name)


class Bucket:
    def __init__(self, cloud, name):
        self.cloud, self.name = cloud, name

    def blob(self, name, generation=None):
        return Blob(self, name, generation)

    def get_blob(self, name, **kwargs):
        data = self.cloud.objects.get((self.name, name))
        if data is None: return None
        b = Blob(self, name, data['generation'])
        b.metadata, b.size = data['metadata'], len(data['content'])
        return b


class Blob:
    def __init__(self, bucket, name, generation=None):
        self.bucket, self.name, self.generation = bucket, name, generation
        self.metadata = {}; self.size = None; self.cache_control = None

    def upload_from_string(self, content, **kwargs):
        assert kwargs['if_generation_match'] == 0
        key = (self.bucket.name, self.name)
        cloud = self.bucket.cloud
        if key in cloud.objects: raise PreconditionFailed('Object already exists')
        self.generation = str(len(cloud.objects) + 1)
        self.size = len(content)
        cloud.objects[key] = {'content': content, 'metadata': self.metadata.copy(), 'generation': self.generation}
        cloud.uploads.append(key)
        if cloud.on_upload: cloud.on_upload()

    def download_as_bytes(self, **kwargs):
        cloud = self.bucket.cloud
        cloud.reads += 1
        data = cloud.objects.get((self.bucket.name, self.name))
        if data is None: raise NotFound('Object not found')
        assert int(data['generation']) == kwargs['if_generation_match'] == int(self.generation)
        if cloud.on_read: cloud.on_read()
        return data['content'][kwargs['start']:kwargs['end'] + 1]
