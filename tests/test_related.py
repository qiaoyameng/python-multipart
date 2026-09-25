from __future__ import annotations

import json
import os
import random
from io import BytesIO

import pytest

from python_multipart.exceptions import FormParserError, MultipartParseError
from python_multipart.multipart import RelatedParser, parse_related

BOUNDARY = b"related-boundary"

ROOT_JSON = json.dumps({"name": "root", "attachments": ["<att-1>", "<att-2>"]}).encode("utf-8")
ATTACHMENT_1 = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x01\x00\x02"
ATTACHMENT_2 = b"\x00\x01\x02\x00\xff\xfe\x00" * 64


def make_part(headers: list[tuple[bytes, bytes]], data: bytes) -> bytes:
    part = b"--" + BOUNDARY + b"\r\n"
    for name, value in headers:
        part += name + b": " + value + b"\r\n"
    part += b"\r\n" + data + b"\r\n"
    return part


def make_message(parts: list[bytes]) -> bytes:
    return b"".join(parts) + b"--" + BOUNDARY + b"--\r\n"


def root_part() -> bytes:
    return make_part(
        [(b"Content-Type", b"application/json"), (b"Content-ID", b"<root>")],
        ROOT_JSON,
    )


def attachment_1() -> bytes:
    return make_part(
        [(b"Content-Type", b"image/png"), (b"Content-ID", b"<att-1>")],
        ATTACHMENT_1,
    )


def attachment_2() -> bytes:
    return make_part(
        [(b"Content-Type", b"application/octet-stream"), (b"Content-ID", b"<att-2>")],
        ATTACHMENT_2,
    )


def feed(parser: RelatedParser, message: bytes, chunk_size: int) -> None:
    for i in range(0, len(message), chunk_size):
        parser.write(message[i : i + chunk_size])
    parser.finalize()


class TestRelatedParser:
    def test_root_part_at_end_selected_by_start(self) -> None:
        message = make_message([attachment_1(), attachment_2(), root_part()])
        parser = RelatedParser(BOUNDARY, start=b"<root>")
        feed(parser, message, 4096)

        assert parser.root is not None
        assert parser.root.content_id == b"<root>"
        assert parser.root.content_type == "application/json"
        assert json.loads(parser.root.data) == {"name": "root", "attachments": ["<att-1>", "<att-2>"]}

        assert [p.content_id for p in parser.parts] == [b"<att-1>", b"<att-2>", b"<root>"]
        assert [p.content_id for p in parser.attachments] == [b"<att-1>", b"<att-2>"]
        assert parser.parts[0].data == ATTACHMENT_1
        assert parser.parts[1].data == ATTACHMENT_2

    def test_root_defaults_to_first_part_without_start(self) -> None:
        message = make_message([root_part(), attachment_1()])
        parser = RelatedParser(BOUNDARY)
        feed(parser, message, 4096)

        assert parser.root is not None
        assert parser.root.content_id == b"<root>"
        assert [p.content_id for p in parser.attachments] == [b"<att-1>"]

    def test_attachment_data_with_null_bytes(self) -> None:
        message = make_message([root_part(), attachment_1(), attachment_2()])
        parser = RelatedParser(BOUNDARY, start=b"<root>")
        feed(parser, message, 4096)

        att1, att2 = parser.attachments
        assert att1.data == ATTACHMENT_1
        assert att1.size == len(ATTACHMENT_1)
        assert att2.data == ATTACHMENT_2
        assert ATTACHMENT_2.count(b"\x00") > 0

    def test_part_headers_and_media_type_kept(self) -> None:
        message = make_message([root_part(), attachment_1()])
        parser = RelatedParser(BOUNDARY)
        feed(parser, message, 4096)

        part = parser.attachments[0]
        assert part.media_type == "image/png"
        assert part.headers[b"content-id"] == b"<att-1>"
        # Parts are not treated as form fields.
        assert part.file.field_name is None

    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 64, 4096])
    def test_chunk_sizes(self, chunk_size: int) -> None:
        message = make_message([attachment_1(), root_part(), attachment_2()])
        parser = RelatedParser(BOUNDARY, start=b"<root>")
        feed(parser, message, chunk_size)

        assert parser.root is not None
        assert parser.root.data == ROOT_JSON
        assert [p.data for p in parser.attachments] == [ATTACHMENT_1, ATTACHMENT_2]

    def test_random_chunking(self) -> None:
        message = make_message([attachment_1(), root_part(), attachment_2()])
        parser = RelatedParser(BOUNDARY, start=b"<root>")
        rng = random.Random(1234)
        pos = 0
        while pos < len(message):
            step = rng.randrange(1, 42)
            parser.write(message[pos : pos + step])
            pos += step
        parser.finalize()

        assert parser.root is not None
        assert parser.root.data == ROOT_JSON
        assert [p.data for p in parser.attachments] == [ATTACHMENT_1, ATTACHMENT_2]

    def test_duplicate_content_id_fails(self) -> None:
        message = make_message([attachment_1(), attachment_1(), root_part()])
        parser = RelatedParser(BOUNDARY, start=b"<root>")
        with pytest.raises(MultipartParseError, match="Duplicate Content-ID"):
            feed(parser, message, 4096)

    def test_missing_root_part_fails(self) -> None:
        message = make_message([attachment_1(), attachment_2()])
        parser = RelatedParser(BOUNDARY, start=b"<no-such-part>")
        parser.write(message)
        with pytest.raises(MultipartParseError, match="Root part not found"):
            parser.finalize()

    def test_root_content_type_mismatch_fails(self) -> None:
        message = make_message([attachment_1(), root_part()])
        parser = RelatedParser(BOUNDARY, start=b"<root>", root_content_type="text/plain")
        parser.write(message)
        with pytest.raises(MultipartParseError, match="does not match declared type"):
            parser.finalize()

    def test_root_content_type_match(self) -> None:
        message = make_message([attachment_1(), root_part()])
        parser = RelatedParser(BOUNDARY, start=b"<root>", root_content_type="application/json")
        feed(parser, message, 4096)
        assert parser.root is not None
        assert parser.root.media_type == "application/json"

    def test_write_after_failure_raises(self) -> None:
        message = make_message([attachment_1(), attachment_1()])
        parser = RelatedParser(BOUNDARY)
        with pytest.raises(MultipartParseError):
            parser.write(message)
        with pytest.raises(FormParserError, match="failed parser"):
            parser.write(b"more data")


class TestRelatedParserFileHandling:
    def test_attachments_spill_to_disk(self, tmp_path: os.PathLike[str]) -> None:
        config = {"MAX_MEMORY_FILE_SIZE": 8, "UPLOAD_DIR": str(tmp_path)}
        message = make_message([attachment_2(), root_part()])
        parser = RelatedParser(BOUNDARY, start=b"<root>", config=config)
        feed(parser, message, 4096)

        att = parser.attachments[0]
        assert not att.file.in_memory
        assert att.data == ATTACHMENT_2
        parser.close()

    def test_parts_closed_on_parse_failure(self, tmp_path: os.PathLike[str]) -> None:
        config = {"MAX_MEMORY_FILE_SIZE": 1, "UPLOAD_DIR": str(tmp_path)}
        message = make_message([attachment_2(), attachment_2(), root_part()])
        parser = RelatedParser(BOUNDARY, start=b"<root>", config=config)

        with pytest.raises(MultipartParseError, match="Duplicate Content-ID"):
            parser.write(message)

        # The completed parts were never handed off to the caller, so their
        # temporary files must have been closed (and deleted).
        assert len(parser.parts) == 1
        assert parser.parts[0].file.file_object.closed
        assert os.listdir(tmp_path) == []

    def test_parts_closed_on_finalize_failure(self, tmp_path: os.PathLike[str]) -> None:
        config = {"MAX_MEMORY_FILE_SIZE": 1, "UPLOAD_DIR": str(tmp_path)}
        message = make_message([attachment_1(), attachment_2()])
        parser = RelatedParser(BOUNDARY, start=b"<missing>", config=config)
        parser.write(message)

        with pytest.raises(MultipartParseError, match="Root part not found"):
            parser.finalize()

        assert len(parser.parts) == 2
        for part in parser.parts:
            assert part.file.file_object.closed
        assert os.listdir(tmp_path) == []

    def test_parts_open_after_success_until_close(self, tmp_path: os.PathLike[str]) -> None:
        config = {"MAX_MEMORY_FILE_SIZE": 1, "UPLOAD_DIR": str(tmp_path)}
        message = make_message([attachment_2(), root_part()])
        parser = RelatedParser(BOUNDARY, start=b"<root>", config=config)
        feed(parser, message, 4096)

        for part in parser.parts:
            assert not part.file.file_object.closed

        parser.close()
        for part in parser.parts:
            assert part.file.file_object.closed
        assert os.listdir(tmp_path) == []

    def test_context_manager_closes_parts(self, tmp_path: os.PathLike[str]) -> None:
        config = {"MAX_MEMORY_FILE_SIZE": 1, "UPLOAD_DIR": str(tmp_path)}
        message = make_message([attachment_2(), root_part()])
        with RelatedParser(BOUNDARY, start=b"<root>", config=config) as parser:
            feed(parser, message, 4096)
        for part in parser.parts:
            assert part.file.file_object.closed


class TestParseRelated:
    def make_headers(self, content_type: bytes) -> dict[str, bytes]:
        return {"Content-Type": content_type}

    def test_parse_related(self) -> None:
        message = make_message([attachment_1(), attachment_2(), root_part()])
        headers = self.make_headers(
            b'multipart/related; boundary="related-boundary"; start="<root>"; type="application/json"'
        )
        parser = parse_related(headers, BytesIO(message), chunk_size=16)

        assert parser.root is not None
        assert parser.root.content_id == b"<root>"
        assert json.loads(parser.root.data) == {"name": "root", "attachments": ["<att-1>", "<att-2>"]}
        assert [p.data for p in parser.attachments] == [ATTACHMENT_1, ATTACHMENT_2]
        parser.close()

    def test_parse_related_respects_content_length(self) -> None:
        message = make_message([root_part(), attachment_1()])
        headers = self.make_headers(b'multipart/related; boundary="related-boundary"')
        headers["Content-Length"] = str(len(message)).encode("latin-1")
        parser = parse_related(headers, BytesIO(message + b"trailing garbage"), chunk_size=16)

        assert parser.root is not None
        assert parser.root.content_id == b"<root>"
        parser.close()

    def test_parse_related_rejects_wrong_content_type(self) -> None:
        headers = self.make_headers(b"multipart/form-data; boundary=foo")
        with pytest.raises(FormParserError, match="Expected multipart/related"):
            parse_related(headers, BytesIO(b""))

    def test_parse_related_requires_content_type(self) -> None:
        with pytest.raises(ValueError, match="No Content-Type"):
            parse_related({}, BytesIO(b""))

    def test_parse_related_requires_boundary(self) -> None:
        headers = self.make_headers(b"multipart/related")
        with pytest.raises(FormParserError, match="No boundary given"):
            parse_related(headers, BytesIO(b""))

    def test_parse_related_failure_closes_parts(self, tmp_path: os.PathLike[str]) -> None:
        message = make_message([attachment_2(), attachment_2()])
        headers = self.make_headers(b'multipart/related; boundary="related-boundary"')
        config = {"MAX_MEMORY_FILE_SIZE": 1, "UPLOAD_DIR": str(tmp_path)}

        with pytest.raises(MultipartParseError, match="Duplicate Content-ID"):
            parse_related(headers, BytesIO(message), config=config)

        assert os.listdir(tmp_path) == []
