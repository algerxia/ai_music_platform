import json

import redis

from app.config import settings


QUEUE_KEY = "studio:jobs"


def redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def enqueue_job(job_id: str) -> None:
    redis_client().lpush(QUEUE_KEY, json.dumps({"job_id": job_id}))


def dequeue_job(timeout: int = 5) -> str | None:
    item = redis_client().brpop(QUEUE_KEY, timeout=timeout)
    if not item:
        return None
    payload = json.loads(item[1])
    return payload.get("job_id")
