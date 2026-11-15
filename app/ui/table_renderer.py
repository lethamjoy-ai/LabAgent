"""Warm renderer dependencies while the user fills in the sign-in form.

Only Python modules are shared. Patient data and prepared tables stay in the
current render call; this worker never accesses Streamlit state or the database.
"""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module


def _load():
    import_module("pandas")
    import_module("pyarrow")


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="table-renderer")
ready = _executor.submit(_load)
