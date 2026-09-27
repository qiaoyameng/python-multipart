"""Chunked multipart parsing with the explicit pause/resume protocol.

This example simulates a receiver whose disk writes are slower than the
network: the ``on_part_data`` callback asks the parser to pause, ``write()``
reports how many bytes were actually consumed, and the caller keeps the
unconsumed remainder and re-feeds it after ``resume()``.

To demonstrate that pausing is transparent, the same multipart body is parsed
with several chunk sizes and pause positions, and each resulting callback
sequence is compared against a plain, non-paused parse.

Run with: python examples/pause_resume.py
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable

from python_multipart import MultipartParser

BOUNDARY = b"boundary"

BODY = (
    b"--boundary\r\n"
    b'Content-Disposition: form-data; name="field"\r\n'
    b"\r\n"
    b"field-value\r\n"
    b"--boundary\r\n"
    b'Content-Disposition: form-data; name="file"; filename="file.txt"\r\n'
    b"Content-Type: text/plain\r\n"
    b"\r\n"
    b"content with --bound and\r\n"
    b"--boundary-lookalike prefixes\r\n"
    b"--boundary--\r\n"
)

Event = tuple[str, bytes]


def make_parser(events: list[Event], pause_on: set[str]) -> MultipartParser:
    """Create a parser that records events and pauses on the given callbacks."""
    parser = MultipartParser(BOUNDARY)

    def notify(name: str) -> Callable[[], None]:
        def callback() -> None:
            events.append((name, b""))
            if name in pause_on:
                parser.pause()

        return callback

    def data(name: str) -> Callable[[bytes, int, int], None]:
        def callback(chunk: bytes, start: int, end: int) -> None:
            events.append((name, chunk[start:end]))
            if name in pause_on:
                # The consumer (e.g. a slow disk) asks the producer to wait.
                parser.pause()

        return callback

    parser.set_callback("part_begin", notify("part_begin"))
    parser.set_callback("part_data", data("part_data"))
    parser.set_callback("part_end", notify("part_end"))
    parser.set_callback("header_begin", notify("header_begin"))
    parser.set_callback("header_field", data("header_field"))
    parser.set_callback("header_value", data("header_value"))
    parser.set_callback("header_end", notify("header_end"))
    parser.set_callback("headers_finished", notify("headers_finished"))
    parser.set_callback("end", notify("end"))
    return parser


def feed(parser: MultipartParser, chunks: Iterable[bytes]) -> None:
    """Feed chunks to the parser, honoring the pause/resume protocol."""
    for chunk in chunks:
        offset = 0
        while offset < len(chunk) or parser.paused:
            if parser.paused:
                # The slow consumer catches up (e.g. the file buffer is
                # flushed to disk), then parsing resumes with the remainder.
                parser.resume()
            offset += parser.write(chunk[offset:])
    parser.finalize()


def normalize(events: list[Event]) -> list[Event]:
    """Merge consecutive data events, so sequences compare chunking-independently."""
    merged: list[Event] = []
    for name, payload in events:
        if payload and merged and merged[-1][0] == name:
            merged[-1] = (name, merged[-1][1] + payload)
        else:
            merged.append((name, payload))
    return merged


def run(chunk_size: int, pause_on: set[str]) -> list[Event]:
    events: list[Event] = []
    parser = make_parser(events, pause_on)
    chunks = [BODY[i : i + chunk_size] for i in range(0, len(BODY), chunk_size)]
    feed(parser, chunks)
    return normalize(events)


def main() -> int:
    baseline = run(len(BODY), set())

    pause_positions = [
        {"part_data"},  # Slow disk writer.
        {"header_field", "header_value"},  # Pause in the middle of headers.
        {"part_end", "part_begin"},  # Pause at boundary matching.
        {"part_data", "header_field", "header_value", "part_begin", "part_end", "end"},  # Pause everywhere.
    ]

    failures = 0
    for chunk_size in (1, 2, 3, 5, 7, 13, 64, len(BODY)):
        for pause_on in pause_positions:
            events = run(chunk_size, pause_on)
            ok = events == baseline
            failures += not ok
            label = ",".join(sorted(pause_on))
            print(f"chunk_size={chunk_size:>3}  pause_on={label:<58}  {'OK' if ok else 'MISMATCH'}")

    if failures:
        print(f"{failures} run(s) diverged from the non-paused parse")
        return 1
    print("All paused runs reproduce the non-paused callback sequence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
