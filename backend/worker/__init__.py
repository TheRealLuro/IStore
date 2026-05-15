"""Out-of-process ML worker.

Lives in `neuthek-ml-worker`, a sibling container to the API. Run as
`python -m backend.worker.main`. See `backend/jobs.py` for the queue
contract.
"""
