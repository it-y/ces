from pathlib import Path, PurePosixPath

import pytest
from fastapi import HTTPException

from app.core.paths import safe_path_join, validate_zip_member


def test_safe_path_join_accepts_child(tmp_path):
    assert safe_path_join(tmp_path, "folder/file.png") == (tmp_path / "folder" / "file.png").resolve()


@pytest.mark.parametrize("value", [
    "../secret.txt",
    "folder/../../secret.txt",
    "/etc/passwd",
    r"C:\\Windows\\win.ini",
    r"..\\secret.txt",
])
def test_safe_path_join_rejects_escape(tmp_path, value):
    with pytest.raises(HTTPException) as exc:
        safe_path_join(tmp_path, value)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("value", [
    "resources/image.png",
    "inputs/nested/photo.webp",
])
def test_validate_zip_member_accepts_relative_posix_path(value):
    assert validate_zip_member(value) == PurePosixPath(value)


@pytest.mark.parametrize("value", [
    "../outside.py",
    "resources/../../outside.py",
    "/absolute/file",
    r"C:\\outside.py",
    r"resources\\..\\outside.py",
    "",
])
def test_validate_zip_member_rejects_unsafe_path(value):
    with pytest.raises(HTTPException) as exc:
        validate_zip_member(value)
    assert exc.value.status_code == 400
