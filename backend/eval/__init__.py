"""Quality-evaluation utilities for the search + summary pipeline.

The operator runs these against a small held-out eval set of
(image_id, expected_queries) pairs to track whether prompt /
ranker / scene-hint changes actually improve recall. Not run in CI
— too dependent on a populated user library + the heavy ML stack
that's optional in test environments. Run manually after material
pipeline changes.
"""
