# Python-Multipart

<p align="center">
<a href="https://github.com/Kludex/python-multipart/actions">
    <img src="https://github.com/Kludex/python-multipart/workflows/CI/badge.svg" alt="Build Status">
</a>
<a href="https://pypi.org/project/python-multipart/">
    <img src="https://badge.fury.io/py/python-multipart.svg" alt="Package version">
</a>
<a href="https://pypi.org/project/python-multipart" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/python-multipart.svg?color=%2334D058" alt="Supported Python versions">
</a>
<a href="https://discord.gg/RxKUF5JuHs">
    <img src="https://img.shields.io/discord/1051468649518616576?logo=discord&logoColor=ffffff&color=7389D8&labelColor=6A7EC2" alt="Discord">
</a>
</p>

Python-Multipart is a streaming multipart parser for Python.

## Quickstart

### Simple Example

The following example shows a quick example of parsing an incoming request body in a simple WSGI application:

```python
import python_multipart

def simple_app(environ, start_response):
    ret = []

    # The following two callbacks just append the name to the return value.
    def on_field(field):
        ret.append(b"Parsed value parameter named: %s" % (field.field_name,))

    def on_file(file):
        ret.append(b"Parsed file parameter named: %s" % (file.field_name,))

    # Create headers object.  We need to convert from WSGI to the actual
    # name of the header, since this library does not assume that you are
    # using WSGI.
    headers = {'Content-Type': environ['CONTENT_TYPE']}
    if 'CONTENT_LENGTH' in environ:
        headers['Content-Length'] = environ['CONTENT_LENGTH']

    # Parse the form.
    python_multipart.parse_form(headers, environ['wsgi.input'], on_field, on_file)

    # Return something.
    start_response('200 OK', [('Content-type', 'text/plain')])
    ret.append(b'\n')
    return ret

from wsgiref.simple_server import make_server
from wsgiref.validate import validator

httpd = make_server('', 8123, simple_app)
print("Serving on port 8123...")
httpd.serve_forever()
```

If you test this with curl, you can see that the parser works:

```console
$ curl -ik -F "foo=bar" http://localhost:8123/
HTTP/1.0 200 OK
Date: Sun, 07 Apr 2013 01:40:52 GMT
Server: WSGIServer/0.1 Python/2.7.3
Content-type: text/plain

Parsed value parameter named: foo
```

For a more in-depth example showing how the various parts fit together, check out the next section.

### In-Depth Example

In this section, we’ll build an application that computes the SHA-256 hash of all uploaded files in a streaming manner.

To start, we need a simple WSGI application. We could do this with a framework like Flask, Django, or Tornado, but for now let’s stick to plain WSGI:

```python
import python_multipart

def simple_app(environ, start_response):
    start_response('200 OK', [('Content-type', 'text/plain')])
    return ['Hashes:\n']

from wsgiref.simple_server import make_server
httpd = make_server('', 8123, simple_app)
print("Serving on port 8123...")
httpd.serve_forever()
```

You can run this and check with curl that it works properly:

```console
$ curl -ik http://localhost:8123/
HTTP/1.0 200 OK
Date: Sun, 07 Apr 2013 01:49:03 GMT
Server: WSGIServer/0.1 Python/2.7.3
Content-type: text/plain
Content-Length: 8

Hashes:
```

Good! It works. Now, let’s add some of the code that we need. What we need to do, essentially, is set up the appropriate parser and callbacks so that we can access each portion of the request as it arrives, without needing to store any parts in memory.

We can start off by checking if we need to create the parser at all - if the Content-Type isn’t multipart/form-data, then we’re not going to do anything.

The final code should look like this:

```python
import hashlib
import python_multipart
from python_multipart.multipart import parse_options_header

def simple_app(environ, start_response):
    ret = []

    # Python 2 doesn't have the "nonlocal" keyword from Python 3, so we get
    # around it by setting attributes on a dummy object.
    class g(object):
        hash = None

    # This is called when a new part arrives.  We create a new hash object
    # in this callback.
    def on_part_begin():
        g.hash = hashlib.sha256()

    # We got some data!  Update our hash.
    def on_part_data(data, start, end):
        g.hash.update(data[start:end])

    # Our current part is done, so we can finish the hash.
    def on_part_end():
        ret.append("Part hash: %s" % (g.hash.hexdigest(),))

    # Parse the Content-Type header to get the multipart boundary.
    content_type, params = parse_options_header(environ['CONTENT_TYPE'])
    boundary = params.get(b'boundary')

    # Callbacks dictionary.
    callbacks = {
        'on_part_begin': on_part_begin,
        'on_part_data': on_part_data,
        'on_part_end': on_part_end,
    }

    # Create the parser.
    parser = python_multipart.MultipartParser(boundary, callbacks)

    # The input stream is from the WSGI environ.
    inp = environ['wsgi.input']

    # Feed the parser with data from the request.
    size = int(environ['CONTENT_LENGTH'])
    while size > 0:
        to_read = min(size, 1024 * 1024)
        data = inp.read(to_read)
        parser.write(data)

        size -= len(data)
        if len(data) != to_read:
            break

    start_response('200 OK', [('Content-type', 'text/plain')])
    return ret

from wsgiref.simple_server import make_server
httpd = make_server('', 8123, simple_app)
print("Serving on port 8123...")
httpd.serve_forever()
```

And you can see that this works:

```console
$ echo "Foo bar" > /tmp/test.txt
$ shasum -a 256 /tmp/test.txt
0b64696c0f7ddb9e3435341720988d5455b3b0f0724688f98ec8e6019af3d931  /tmp/test.txt
$ curl -ik -F file=@/tmp/test.txt http://localhost:8123/
HTTP/1.0 200 OK
Date: Sun, 07 Apr 2013 02:09:10 GMT
Server: WSGIServer/0.1 Python/2.7.3
Content-type: text/plain

Hashes:
Part hash: 0b64696c0f7ddb9e3435341720988d5455b3b0f0724688f98ec8e6019af3d931
```


## multipart/related

Besides `multipart/form-data`, this library can parse
[`multipart/related`](https://datatracker.ietf.org/doc/html/rfc2387) messages:
a root document (often JSON) plus attachments that the root references by
`Content-ID`. The root is *not* required to be the first part - it is located
using the `start` parameter of the enclosing `Content-Type` header, falling
back to the first part when `start` is absent. When a `type` parameter is
present, the root part's media type must match it.

Parts are never treated as named form controls: each one is returned as a
[`RelatedPart`][python_multipart.RelatedPart] carrying its media type,
Content-ID and raw data. The data is held in the same
[`File`][python_multipart.multipart.File] storage as uploads (memory up to
`MAX_MEMORY_FILE_SIZE`, then a temporary file on disk), so finding the root
never requires buffering all attachments in memory.

```python
from io import BytesIO
from python_multipart import parse_related

headers = {
    "Content-Type": (
        b'multipart/related; boundary="B"; '
        b'start="<root@example>"; type="application/json"'
    )
}
body = (
    b"--B\r\n"
    b"Content-Type: image/png\r\nContent-ID: <logo@example>\r\n\r\n"
    b"\x89PNG...\r\n"
    b"--B\r\n"
    b"Content-Type: application/json\r\nContent-ID: <root@example>\r\n\r\n"
    b'{"logo": "cid:logo@example"}\r\n'
    b"--B--\r\n"
)

root, parts = parse_related(headers, BytesIO(body))
assert root.content_id == b"root@example"
assert root.content_type == b"application/json"
document = root.read()

for part in parts:
    part.close()  # caller owns the parts, including any temp files
```

Duplicate Content-IDs, a `start` parameter that names no part, a missing
closing boundary, or a root whose media type disagrees with the declared
`type` all raise an error. When that happens, every part that has not yet
been handed back is closed, so backing temporary files are not leaked. For
streaming use, drive [`RelatedParser`][python_multipart.RelatedParser]
directly with `write()`/`finalize()` and receive parts through its
`on_part`/`on_end` callbacks.


## Historical note

This package used to be accessed via `import multipart`. This still works for
now (with a warning) as long as the Python package `multipart` is not also
installed. If both are installed, you need to use the full PyPI name
`python_multipart` for this package.
