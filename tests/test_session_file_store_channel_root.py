from __future__ import annotations
from pathlib import Path
import pytest
from lib.channels.postgres_channel import _resolve_sfs_base
from workspace.utils.session_file_store import SessionFileStore

@pytest.mark.parametrize("suffix",["data_store/cache/sessions","data_store/cache","data_store"])
def test_channel_store_uses_single_cache_directory(tmp_path,suffix):
    configured=tmp_path/suffix
    store=SessionFileStore(_resolve_sfs_base(configured))
    assert store.base == tmp_path/"data_store/cache/sessions"
