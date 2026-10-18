# Celery is disabled for now. The worker, Redis broker and evaluate_account task are commented out.
#
# from celery import Celery
#
# from app.config import get_settings
#
# celery_app = Celery(
#     "aml_copilot",
#     broker=get_settings().redis_url,
#     backend=get_settings().redis_url,
#     include=["app.tasks.assess"],
# )
# celery_app.conf.update(
#     task_acks_late=True,  # a crashed worker re-queues the task
#     task_reject_on_worker_lost=True,
#     task_time_limit=60 * 15,  # hard stop per account
#     worker_prefetch_multiplier=1,
# )
