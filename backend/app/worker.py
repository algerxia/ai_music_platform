import time
import traceback

from app.db import init_db
from app.jobs import process_job
from app.queue import dequeue_job
from app.storage import ensure_bucket


def main() -> None:
    init_db()
    try:
        ensure_bucket()
    except Exception as exc:  # noqa: BLE001
        print(f"MinIO not ready yet: {exc}")
    print("Studio AI worker started")
    while True:
        job_id = dequeue_job(timeout=5)
        if not job_id:
            continue
        print(f"Processing job {job_id}")
        try:
            process_job(job_id)
            print(f"Job {job_id} finished")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            time.sleep(1)


if __name__ == "__main__":
    main()
