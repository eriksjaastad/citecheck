"""Fixtures: a tiny PDF builder and a local HTTP server, so no test leaves the machine."""
from __future__ import annotations

import http.server
import threading
import urllib.request

import pytest


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF, one Helvetica text line per page (ASCII only)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        page_no, content_no = len(objs) + 1, len(objs) + 2
        kids.append(f"{page_no} 0 R")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {content_no} 0 R /Resources << /Font << /F1 FONT 0 R >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_no = len(objs)
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body.replace('FONT', str(font_no))}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


ROUTES: dict[str, tuple[int, str, bytes]] = {
    "/page": (200, "text/html; charset=utf-8",
              (b"<html><script>var x='hidden words here';</script>"
               b"<p>The council voted <b>seven to two</b> to approve the budget.</p></html>")),
    "/blank": (200, "text/html", b"<html><script>render()</script></html>"),
    "/doc.pdf": (200, "application/pdf", make_pdf(["Cover page", "Revenue rose sharply in Q3"])),
    "/forbidden": (403, "text/html", b"no"),
    "/limited": (429, "text/html", b"slow down"),
    "/gone": (404, "text/html", b"missing"),
    "/broken": (500, "text/html", b"oops"),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/to-ftp":
            self.send_response(302)
            self.send_header("Location", "ftp://127.0.0.1/x")
            self.end_headers()
            return
        code, ctype, body = ROUTES.get(self.path, (404, "text/plain", b""))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def no_network(monkeypatch):
    """Fail any test that opens a connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("network used without network=True")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
