from __future__ import annotations


class FormParserError(ValueError):
    """Base error class for our form parser."""


class ParseError(FormParserError):
    """This exception (or a subclass) is raised when there is an error while
    parsing something.

    Attributes:
        offset: The byte offset at which the error was detected. For errors raised while
            writing a chunk this is relative to that chunk; for errors raised by
            [`MultipartParser.finalize`][python_multipart.MultipartParser.finalize] it is the
            absolute byte position within the whole input stream. -1 if unknown.
        phase: The phase of parsing during which the error occurred (e.g. "headers",
            "part_data" or "boundary" for multipart messages), or None if not applicable.
    """

    def __init__(self, message: str, *, offset: int = -1, phase: str | None = None) -> None:
        super().__init__(message)
        self.offset = offset
        self.phase = phase


class MultipartParseError(ParseError):
    """This is a specific error that is raised when the MultipartParser detects
    an error while parsing.
    """


class QuerystringParseError(ParseError):
    """This is a specific error that is raised when the QuerystringParser
    detects an error while parsing.
    """


class DecodeError(ParseError):
    """This exception is raised when there is a decoding error - for example
    with the Base64Decoder or QuotedPrintableDecoder.
    """


class FileError(FormParserError, OSError):
    """Exception class for problems with the File class."""
