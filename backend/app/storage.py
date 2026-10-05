from pathlib import Path
import shutil
import uuid

from app.config import settings


def data_root() -> Path:
    return Path(settings.data_dir)


def ensure_bucket() -> None:
    (data_root() / "assets").mkdir(parents=True, exist_ok=True)


def upload_file(local_path: Path, content_type: str = "audio/mpeg") -> str:
    ensure_bucket()
    key = f"assets/{uuid.uuid4().hex}/{local_path.name}"
    target = data_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(local_path, target)
    return key


def upload_bytes(data: bytes, filename: str, content_type: str = "audio/mpeg") -> str:
    ensure_bucket()
    key = f"assets/{uuid.uuid4().hex}/{filename}"
    target = data_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return key


def public_url(key: str) -> str:
    return f"/files/{key}"


def download_bytes(key: str) -> bytes:
    path = data_root() / key
    if not path.exists():
        raise FileNotFoundError(key)
    return path.read_bytes()
