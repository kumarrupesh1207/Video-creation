import pytest
from pydantic import ValidationError

import server


def test_create_request_validation():
    for bad in ({"aspect": "4:3"}, {"aspect": "custom", "width": 99999}, {"prompt": "x" * 1001}, {"provider": "evil"}):
        with pytest.raises(ValidationError):
            server.CreateRequest(**{"prompt": "x", **bad})


def test_create_rejects_empty_prompt_and_foreign_music():
    assert server.create_single(server.CreateRequest(prompt="  "))["status"] == "error"
    r = server.create_single(server.CreateRequest(prompt="x", music_path="C:/Windows/win.ini"))
    assert r["status"] == "error"


def test_files_endpoint_blocks_traversal():
    with pytest.raises(server.HTTPException):
        server.get_output_file("../config.yaml")
    assert server.gallery()["items"] is not None
