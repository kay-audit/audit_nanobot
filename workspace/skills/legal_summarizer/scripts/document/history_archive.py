"""Bounded ZIP validation shared by attachment and DOCX readers."""
from __future__ import annotations

import stat
from pathlib import PurePosixPath, PureWindowsPath

MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_RATIO = 500


def validate_archive(archive):
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ValueError("Слишком много записей ZIP")
    seen, total = set(), 0
    for info in infos:
        name = info.orig_filename
        path = PurePosixPath(name)
        key = name.rstrip('/').casefold()
        if (not name or name != info.filename or chr(92) in name or path.is_absolute() or '..' in path.parts
                or PureWindowsPath(name).drive or ':' in name or key in seen
                or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1):
            raise ValueError("Небезопасная или неоднозначная запись ZIP")
        seen.add(key)
        total += info.file_size
        if (info.file_size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES
                or info.file_size > max(1, info.compress_size) * MAX_RATIO):
            raise ValueError("ZIP превышает допустимый объём или коэффициент сжатия")
    return infos
