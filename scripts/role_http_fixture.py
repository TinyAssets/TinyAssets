"""Local TLS peer for production-copy acceptance; never replaces platform code."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ADDRESS = '93.184.216.34'  # Global-address policy still runs; this alias has no uplink.
HOSTS = ('api.github.com', 'stream.oracle.test')


def certificates():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    directory = tempfile.TemporaryDirectory(prefix='role-http-oracle-')
    root = Path(directory.name)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), False)
            .sign(key, hashes.SHA256()))
    (root / 'ca.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (root / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (root / 'hosts').write_text('127.0.0.1 localhost\n' + ADDRESS + ' ' + ' '.join(HOSTS) + '\n')
    return directory


SERVER = r'''
import json, ssl, threading
calls = []
class Service(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self.reply(200, {'calls': len(calls)})
    def do_POST(self):
        size = int(self.headers['Content-Length'])
        assert 0 < size < 1024 * 1024
        body = json.loads(self.rfile.read(size))
        assert body['title'] and body['body']
        assert self.headers.get('Authorization')
        calls.append(self.path)  # Never retain credentials or restored conversation content.
        self.reply(201, {'number': 123456,
            'html_url': 'https://github.com/TinyAssets/TinyAssets/issues/123456'})
tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
tls.load_cert_chain('/fixture/ca.pem', '/fixture/key.pem')
service = http.server.ThreadingHTTPServer(('0.0.0.0', 443), Service)
service.socket = tls.wrap_socket(service.socket, server_side=True)
threading.Thread(target=service.serve_forever, daemon=True).start()
'''
