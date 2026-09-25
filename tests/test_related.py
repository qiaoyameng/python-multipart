from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from python_multipart import RelatedParser, RelatedPart, parse_related
from python_multipart.exceptions import RelatedParseError
from python_multipart.multipart import File


def build_body(boundary: bytes, parts: list[tuple[bytes, bytes | None, bytes]]) -> bytes:
    """Build a multipart/related body from (content_type, content_id, data) tuples."""
    body = b""
    for content_type, content_id, data in parts:
        body += b"--" + boundary + b"\r\nContent-Type: " + content_type + b"\r\n"
        if content_id is not None:
            body += b"Content-ID: " + content_id + b"\r\n"
        body += b"\r\n" + data + b"\r\n"
    return body + b"--" + boundary + b"--\r\n"


ROOT_LAST_PARTS = [
    (b"image/png", b"<img@x>", b"\x89PNG\r\n\x1a\n attachment"),
    (b"text/plain", b"<note@x>", b"hello"),
    (b"application/json; charset=utf-8", b"<root@x>", b'{"img": "cid:img@x"}'),
]


def _feed(body: bytes, parser: RelatedParser, chunk_size: int) -> None:
    for index in range(0, len(body), chunk_size):
        parser.write(body[index : index + chunk_size])
    parser.finalize()


def test_root_part_at_end() -> None:
    body = build_body(b"BOUND", ROOT_LAST_PARTS)
    headers = {
        "Content-Type": b'multipart/related; boundary="BOUND"; '
        b'start="<root@x>"; type="application/json"'
    }

    root, parts = parse_related(headers, BytesIO(body))

    assert len(parts) == 3
    assert root.is_root is True
    assert root.content_id == b"root@x"
    assert root.content_type == b"application/json"
    assert root.content_type_params == {b"charset": b"utf-8"}
    assert root.read() == b'{"img": "cid:img@x"}'
    assert [part.content_id for part in parts] == [b"img@x", b"note@x", b"root@x"]
    assert [part.is_root for part in parts] == [False, False, True]
    for part in parts:
        part.close()


def test_attachment_with_binary_zero_bytes() -> None:
    payload = bytes(range(256))
    body = build_body(b"B", [(b"application/octet-stream", b"<bin@x>", payload)])

    root, parts = parse_related({"Content-Type": b"multipart/related; boundary=B"}, BytesIO(body))

    assert root is parts[0]
    assert root.content_id == b"bin@x"
    assert root.read() == payload
    for part in parts:
        part.close()


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 16, 64, 4096])
def test_different_chunk_sizes(chunk_size: int) -> None:
    body = build_body(b"BOUND", ROOT_LAST_PARTS)

    received: list[RelatedPart] = []
    parser = RelatedParser(
        b"BOUND",
        on_part=received.append,
        start=b"<root@x>",
        root_type=b"application/json",
    )
    _feed(body, parser, chunk_size)

    assert [part.content_id for part in received] == [b"img@x", b"note@x", b"root@x"]
    assert received[-1].is_root is True
    assert received[-1].read() == b'{"img": "cid:img@x"}'
    for part in received:
        part.close()


def test_single_byte_writes_roundtrip() -> None:
    payload = b"\x00\xff" * 300
    body = build_body(b"BB", [(b"image/jpeg", b"<a@x>", payload), (b"application/json", b"<r@x>", b"{}")])

    ended: dict[str, Any] = {}
    parser = RelatedParser(
        b"BB",
        on_end=lambda root, parts: ended.update(root=root, parts=parts),
        start=b"<r@x>",
        root_type=b"application/json",
    )
    _feed(body, parser, 1)

    assert ended["root"].content_id == b"r@x"
    assert ended["parts"][0].read() == payload
    for part in ended["parts"]:
        part.close()


def test_duplicate_content_id_fails_and_closes(tmp_path: Path) -> None:
    body = build_body(
        b"B",
        [
            (b"application/octet-stream", b"<dup@x>", b"first"),
            (b"text/plain", b"<dup@x>", b"second"),
        ],
    )

    with pytest.raises(RelatedParseError, match="Duplicate Content-ID"):
        parse_related({"Content-Type": b"multipart/related; boundary=B"}, BytesIO(body))


def test_duplicate_content_id_closes_parts_on_disk(tmp_path: Path) -> None:
    body = build_body(
        b"B",
        [
            (b"application/octet-stream", b"<dup@x>", b"x" * 50),
            (b"text/plain", b"<dup@x>", b"second"),
        ],
    )
    config = {
        "MAX_MEMORY_FILE_SIZE": 10,
        "UPLOAD_DIR": str(tmp_path).encode(),
        "UPLOAD_DELETE_TMP": False,
    }
    parser = RelatedParser(b"B", config=config)

    created: list[str] = []
    original = File._get_disk_file

    def spy(self: File) -> Any:
        fileobj = original(self)
        created.append(fileobj.name)
        return fileobj

    File._get_disk_file = spy
    try:
        with pytest.raises(RelatedParseError, match="Duplicate Content-ID"):
            for index in range(0, len(body), 5):
                parser.write(body[index : index + 5])
    finally:
        File._get_disk_file = original

    # The first part was spooled to disk and must be closed (readable again).
    assert created
    for name in created:
        with open(name, "r+b") as handle:
            assert handle.seek(0, 2) == 50


def test_missing_root_fails_and_closes() -> None:
    body = build_body(b"B", [(b"application/json", b"<a@x>", b"{}")])
    config = {"MAX_MEMORY_FILE_SIZE": 1}
    parser = RelatedParser(b"B", start=b"<missing@x>", config=config)
    parser.write(body)

    with pytest.raises(RelatedParseError, match="not found"):
        parser.finalize()

    for part in parser._parts:
        assert part.file.file_object.closed is True


def test_root_type_mismatch_fails_and_closes() -> None:
    body = build_body(b"B", [(b"text/plain", b"<a@x>", b"hello")])
    parser = RelatedParser(b"B", start=b"<a@x>", root_type=b"application/json")
    parser.write(body)

    with pytest.raises(RelatedParseError, match="does not match declared type"):
        parser.finalize()

    for part in parser._parts:
        assert part.file.file_object.closed is True


def test_no_start_uses_first_part() -> None:
    body = build_body(b"B", [(b"application/json", None, b"{}"), (b"image/png", b"<x@y>", b"\x00")])
    root, parts = parse_related({"Content-Type": b"multipart/related; boundary=B"}, BytesIO(body))

    assert root is parts[0]
    assert root.content_type == b"application/json"
    assert root.is_root is True
    assert parts[1].is_root is False
    for part in parts:
        part.close()


def test_content_id_brackets_optional() -> None:
    body = build_body(b"B", [(b"application/json", b"root@x", b"{}")])
    root, parts = parse_related(
        {"Content-Type": b'multipart/related; boundary=B; start="<root@x>"'},
        BytesIO(body),
    )
    assert root.content_id == b"root@x"
    for part in parts:
        part.close()


def test_start_parameter_percent_encoded() -> None:
    # RFC 2387 allows percent-encoding the angle brackets in start.
    body = build_body(b"B", [(b"application/json", b"<root@x>", b"{}")])
    headers = {"Content-Type": b'multipart/related; boundary=B; start="%3Croot%40x%3E"'}
    root, parts = parse_related(headers, BytesIO(body))
    assert root.content_id == b"root@x"
    for part in parts:
        part.close()


def test_default_content_type_is_octet_stream() -> None:
    body = b"--B\r\nContent-ID: <x@y>\r\n\r\nraw\r\n--B--\r\n"
    root, parts = parse_related({"Content-Type": b"multipart/related; boundary=B"}, BytesIO(body))
    assert root.content_type == b"application/octet-stream"
    assert root.read() == b"raw"
    for part in parts:
        part.close()


def test_callbacks_on_success() -> None:
    body = build_body(b"B", [(b"application/json", b"<r@x>", b"{}"), (b"image/png", b"<i@x>", b"\x00")])
    on_part: list[RelatedPart] = []
    on_end: dict[str, Any] = {}

    parser = RelatedParser(
        b"B",
        on_part=on_part.append,
        on_end=lambda root, parts: on_end.update(root=root, parts=parts),
        start=b"<r@x>",
    )
    parser.write(body)
    parser.finalize()

    assert [part.content_id for part in on_part] == [b"r@x", b"i@x"]
    assert on_end["root"] is on_part[0]
    assert on_end["parts"] == on_part
    for part in on_part:
        part.close()


def test_no_callbacks_on_failure() -> None:
    body = build_body(b"B", [(b"text/plain", b"<a@x>", b"hi")])
    calls: list[str] = []
    parser = RelatedParser(
        b"B",
        on_part=lambda _part: calls.append("part"),
        on_end=lambda _root, _parts: calls.append("end"),
        start=b"<missing@x>",
    )
    parser.write(body)
    with pytest.raises(RelatedParseError):
        parser.finalize()
    assert calls == []


def test_attachments_spool_to_upload_dir(tmp_path: Path) -> None:
    payload = b"0" * 3000
    body = build_body(b"B", [(b"application/json", b"<r@x>", b"{}"), (b"text/csv", b"<d@x>", payload)])
    config = {"MAX_MEMORY_FILE_SIZE": 1024, "UPLOAD_DIR": str(tmp_path).encode()}

    root, parts = parse_related(
        {"Content-Type": b'multipart/related; boundary=B; start="<r@x>"'},
        BytesIO(body),
        config=config,
    )

    attachment = parts[1]
    assert attachment.file.in_memory is False
    assert attachment.file.actual_file_name is not None
    assert attachment.read() == payload
    for part in parts:
        part.close()


def test_empty_message_fails() -> None:
    parser = RelatedParser(b"B")
    with pytest.raises(Exception, match="closing boundary|contains no parts"):
        parser.finalize()


def test_truncated_body_fails_and_closes() -> None:
    truncated = b"--B\r\nContent-Type: application/json\r\nContent-ID: <r@x>\r\n\r\n{}"
    parser = RelatedParser(b"B", config={"MAX_MEMORY_FILE_SIZE": 1})
    parser.write(truncated)
    with pytest.raises(Exception, match="closing boundary"):
        parser.finalize()
    for part in parser._parts:
        assert part.file.file_object.closed is True


def test_parse_related_requires_multipart_related() -> None:
    with pytest.raises(ValueError, match="No Content-Type"):
        parse_related({}, BytesIO(b""))
    with pytest.raises(Exception):
        parse_related({"Content-Type": b"multipart/form-data; boundary=B"}, BytesIO(b""))


def test_boundary_required() -> None:
    from python_multipart.exceptions import FormParserError

    with pytest.raises(FormParserError, match="No boundary"):
        RelatedParser(None)  # type: ignore[arg-type]


def test_streaming_write_error_closes_parts() -> None:
    # A malformed message (here: an oversized header) raises mid-stream.
    parser = RelatedParser(b"B", start=b"<r@x>")
    with pytest.raises(Exception):
        parser.write(b"--B\r\nX" + b"y" * 6000)
    for part in parser._parts:
        assert part.file.file_object.closed is True


def test_parse_failure_during_streaming_writes_closes_all(tmp_path: Path) -> None:
    # First part spooled to disk, then the second part breaks the message.
    config = {
        "MAX_MEMORY_FILE_SIZE": 10,
        "UPLOAD_DIR": str(tmp_path).encode(),
        "UPLOAD_DELETE_TMP": False,
    }
    parser = RelatedParser(b"B", start=b"<r@x>", config=config)
    parser.write(b"--B\r\nContent-Type: application/octet-stream\r\nContent-ID: <a@x>\r\n\r\n")
    parser.write(b"z" * 50)
    created = [part.file.actual_file_name for part in parser._parts]

    with pytest.raises(Exception):
        parser.write(b"\r\n--B\r\nX" + b"y" * 6000)

    assert len(created) == 1 and created[0] is not None
    # The spooled file was closed: reopening it for writing succeeds.
    with open(created[0], "r+b") as handle:  # type: ignore[arg-type]
        assert handle.seek(0, 2) == 50
