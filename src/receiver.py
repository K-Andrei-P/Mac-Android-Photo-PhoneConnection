import sys
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
 
# ---- Configuration ----
DEFAULT_PORT = 8000
DEFAULT_TARGET_DIR = os.path.expanduser("~/Documents/LectureNotes")
 
 
def parse_multipart(body: bytes, content_type: str):
    """
    Manually parse a multipart/form-data body.
 
    Returns a list of dicts: [{"name": ..., "filename": ..., "data": bytes}, ...]
    Only handles the common case of one or more file/form fields separated
    by a boundary -- enough for a phone/Pi client posting a single photo.
    """
    # Extract the boundary from the Content-Type header
    match = re.search(r'boundary=(.+)', content_type)
    if not match:
        raise ValueError("No boundary found in Content-Type header")
    boundary = match.group(1).strip()
    # Boundaries in the body are prefixed with --
    boundary_bytes = ("--" + boundary).encode()
 
    parts = body.split(boundary_bytes)
    fields = []
 
    for part in parts:
        # Skip preamble/epilogue and the closing "--" marker
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
 
        # Each part is: headers \r\n\r\n data
        header_blob, _, data = part.partition(b"\r\n\r\n")
        if not header_blob:
            continue
 
        headers_text = header_blob.decode(errors="replace")
        disposition_match = re.search(
            r'name="([^"]*)"(?:; filename="([^"]*)")?', headers_text
        )
        if not disposition_match:
            continue
 
        name = disposition_match.group(1)
        filename = disposition_match.group(2)
 
        # Strip the trailing \r\n that precedes the next boundary
        if data.endswith(b"\r\n"):
            data = data[:-2]
 
        fields.append({"name": name, "filename": filename, "data": data})
 
    return fields
 
 
class ReceiverHandler(BaseHTTPRequestHandler):
    target_dir = DEFAULT_TARGET_DIR
 
    def do_GET(self):
        # Health Check
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Receiver is running. POST a file to /upload.\n")
 
    def do_POST(self):
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))
 
        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return
 
        body = self.rfile.read(content_length)
 
        try:
            fields = parse_multipart(body, content_type)
        except ValueError as e:
            self.send_error(400, str(e))
            return
 
        saved_files = []
        for field in fields:
            if not field["filename"]:
                continue  # this is a regular form field, not a file
 
            # Guard against empty filenames or path traversal tricks
            safe_name = os.path.basename(field["filename"])
            if not safe_name:
                continue
 
            dest_path = os.path.join(self.target_dir, safe_name)
            os.makedirs(self.target_dir, exist_ok=True)
 
            with open(dest_path, "wb") as f:
                f.write(field["data"])
 
            saved_files.append(safe_name)
            print(f"Saved: {dest_path} ({len(field['data'])} bytes)")
 
        if saved_files:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"OK: saved {', '.join(saved_files)}\n".encode())
        else:
            self.send_error(400, "No file field found in upload")
 
    def log_message(self, format, *args):
        # Quieter, cleaner console logging
        print(f"[{self.client_address[0]}] {format % args}")
 
 
def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    target_dir = os.path.expanduser(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TARGET_DIR
    host = sys.argv[3] if len(sys.argv) > 3 else "0.0.0.0"
 
    os.makedirs(target_dir, exist_ok=True)
    ReceiverHandler.target_dir = target_dir
 
    server = ThreadingHTTPServer((host, port), ReceiverHandler)
    print(f"Listening on {host}:{port}, saving files to: {target_dir}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
 
 
if __name__ == "__main__":
    main()
    
