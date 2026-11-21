# -*- coding: utf-8 -*-
"""Gateway pembayaran GoPay Merchant — layanan HTTP Python murni (stdlib).

Meniru permukaan API gateway Node yang diaudit, tapi seluruh kode terbaca:
- POST /create-qris      (API key) buat QRIS dinamis dari QRIS statis
- GET  /qr/<id>.png      (publik)  gambar QR (butuh segno/qrcode terpasang)
- GET  /api/qr-status/<id> (publik) status pembayaran per QRIS
- POST /check-payment    (API key) cek pembayaran per nominal + scope klaim
- GET  /transactions     (API key) mutasi terakhir (rupiah)
- GET  /token-status     (API key) kesehatan sesi GoBiz
- GET  /health           (publik)

Prinsip polling santun: TIDAK ada loop polling latar. Mutasi GoBiz hanya
ditarik saat ada panggilan status/check yang masuk.
"""
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import zlib

import qris as qris_mod
from gobiz import GoBizClient, SessionStore, GoBizError

BASE = os.path.dirname(os.path.abspath(__file__))
API_KEY = os.environ.get('GOPAY_GW_API_KEY', '')
QRIS_STATIC = os.environ.get('QRIS_STATIC', '')
PORT = int(os.environ.get('GOPAY_GW_PORT', '8890'))
EXPIRY_SECONDS = int(os.environ.get('QRIS_EXPIRY_SECONDS', '600'))
SESSION_PATH = os.environ.get('GOPAY_GW_SESSION',
                              os.path.join(BASE, 'data', 'session.json'))
STATE_PATH = os.environ.get('GOPAY_GW_STATE',
                            os.path.join(BASE, 'data', 'state.json'))

client = GoBizClient(SessionStore(SESSION_PATH))
_lock = threading.Lock()


# ---------------- state (pending + klaim) ----------------
def _load_state():
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            s = json.load(f)
        return {'pending': s.get('pending') or {},
                'claimed': s.get('claimed') or {}}
    except (OSError, ValueError):
        return {'pending': {}, 'claimed': {}}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


state = _load_state()


def _iso(ts_epoch):
    return datetime.fromtimestamp(ts_epoch, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%S.000Z')


def _parse_tx_time(value):
    if not value:
        return 0
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except ValueError:
        return 0


# ---------------- pencocokan pembayaran ----------------
def try_match(pending, scope_id):
    """Cari mutasi yang cocok untuk satu pending; klaim atomik per trx."""
    start = int(pending['created_at']) - 90  # toleransi clock drift
    txs = client.fetch_transactions(_iso(start), _iso(time.time()),
                                    size=30)
    for tx in txs:
        if tx['status'] not in ('settlement', 'capture'):
            continue
        if tx['amount'] != int(pending['amount']):
            continue
        if _parse_tx_time(tx['time']) < int(pending['created_at']) - 90:
            continue
        txid = tx['transaction_id']
        if not txid:
            continue
        owner = state['claimed'].get(txid)
        if owner and owner != scope_id:
            continue  # sudah diklaim pembayaran lain
        state['claimed'][txid] = scope_id
        return tx
    return None


def check_pending(qris_id):
    with _lock:
        p = state['pending'].get(qris_id)
        if not p:
            return {'success': False, 'status': 'NOT_FOUND'}
        if p.get('status') == 'PAID':
            return {'success': True, 'paid': True, 'status': 'PAID',
                    'transaction': p.get('transaction')}
        if time.time() > p['expires_at']:
            del state['pending'][qris_id]
            _save_state(state)
            return {'success': True, 'paid': False, 'status': 'EXPIRED'}
        tx = try_match(p, p['trx_id'])
        if tx:
            p['status'] = 'PAID'
            p['transaction'] = tx
            _save_state(state)
            return {'success': True, 'paid': True, 'status': 'PAID',
                    'transaction': tx}
        _save_state(state)
        return {'success': True, 'paid': False, 'status': 'PENDING'}


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        print('[%s] %s' % (datetime.now().isoformat(timespec='seconds'),
                           fmt % args), flush=True)

    def _send(self, code, obj, ctype='application/json'):
        if ctype == 'application/json':
            body = json.dumps(obj).encode('utf-8')
        else:
            body = obj
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return bool(API_KEY) and \
            self.headers.get('x-api-key') == API_KEY

    def _body(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b''
        try:
            return json.loads(raw.decode('utf-8')) if raw else {}
        except ValueError:
            return {}

    # ----- routes -----
    def do_GET(self):
        path = self.path.split('?')[0]
        try:
            if path == '/health':
                return self._send(200, {'ok': True,
                                        'service': 'gopay-gateway-py'})
            if path.startswith('/api/qr-status/'):
                return self._send(200,
                                  check_pending(path.rsplit('/', 1)[1]))
            if path.startswith('/qr/') and path.endswith('.png'):
                return self._qr_png(path.split('/')[2][:-4])
            if path == '/token-status':
                if not self._authed():
                    return self._send(401, {'success': False,
                                            'error': 'unauthorized'})
                return self._token_status()
            if path == '/transactions':
                if not self._authed():
                    return self._send(401, {'success': False,
                                            'error': 'unauthorized'})
                return self._transactions()
            return self._send(404, {'success': False,
                                    'error': 'not found'})
        except GoBizError as e:
            return self._send(502, {'success': False, 'error': str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {'success': False, 'error': str(e)})

    def do_POST(self):
        path = self.path.split('?')[0]
        try:
            if path == '/create-qris':
                if not self._authed():
                    return self._send(401, {'success': False,
                                            'error': 'unauthorized'})
                return self._create_qris()
            if path == '/check-payment':
                if not self._authed():
                    return self._send(401, {'success': False,
                                            'error': 'unauthorized'})
                return self._check_payment()
            return self._send(404, {'success': False,
                                    'error': 'not found'})
        except GoBizError as e:
            return self._send(502, {'success': False, 'error': str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {'success': False, 'error': str(e)})

    # ----- handlers -----
    def _create_qris(self):
        if not QRIS_STATIC:
            return self._send(500, {'success': False,
                                    'error': 'QRIS_STATIC belum diatur'})
        body = self._body()
        base_amount = int(body.get('base_amount') or 0)
        amount = int(body.get('amount') or 0)
        unique_code = 0
        now = time.time()
        with _lock:
            if base_amount > 0:
                # Kode unik: suffix 1..500 (maks Rp 500) yang membuat total
                # bayar berbeda dari semua pending aktif lain -> pencocokan
                # nominal tak pernah tertukar antar pengguna. Bila pemesan
                # mengirim account_key, kode diutamakan khas akun itu
                # (hash stabil) supaya tiap akun beda dan konsisten; geser
                # ke kode bebas berikutnya hanya bila totalnya sedang
                # dipakai pending lain.
                used = {p['amount'] for p in state['pending'].values()
                        if p.get('expires_at', 0) > now}
                account_key = str(body.get('account_key') or '')
                if account_key:
                    start = zlib.crc32(
                        account_key.encode('utf-8')) % 500 + 1
                else:
                    start = secrets.randbelow(500) + 1
                chosen = None
                for i in range(500):
                    cand = (start - 1 + i) % 500 + 1
                    if base_amount + cand not in used:
                        chosen = cand
                        break
                if chosen is None:
                    return self._send(409, {'success': False,
                                            'error': 'kode unik habis'})
                unique_code = chosen
                amount = base_amount + unique_code
            if amount <= 0:
                return self._send(400, {'success': False,
                                        'error': 'nominal tidak valid'})
            code = qris_mod.inject_amount(QRIS_STATIC, amount)
            qris_id = secrets.token_hex(5)
            trx_id = 'TRX-' + secrets.token_hex(4).upper()
            state['pending'][qris_id] = {
                'qris_id': qris_id, 'trx_id': trx_id, 'amount': amount,
                'base_amount': base_amount or amount,
                'unique_code': unique_code,
                'qris_code': code, 'status': 'PENDING',
                'created_at': now, 'expires_at': now + EXPIRY_SECONDS,
            }
            _save_state(state)
        return self._send(200, {
            'success': True, 'qris_id': qris_id, 'trx_id': trx_id,
            'qris_code': code, 'amount': amount,
            'base_amount': base_amount or amount,
            'unique_code': unique_code,
            'merchant_name': qris_mod.merchant_name(QRIS_STATIC),
            'expires_at': now + EXPIRY_SECONDS,
            'qr_png_url': '/qr/%s.png' % qris_id,
            'status_url': '/api/qr-status/%s' % qris_id,
        })

    def _check_payment(self):
        body = self._body()
        amount = int(body.get('amount') or 0)
        if amount <= 0:
            return self._send(400, {'success': False,
                                    'error': 'nominal tidak valid'})
        start = int(body.get('start_time') or (time.time() - 3600))
        scope = body.get('trx_id') or ('manual-%d' % int(time.time()))
        pseudo = {'amount': amount, 'created_at': start}
        with _lock:
            tx = try_match(pseudo, scope)
            _save_state(state)
        if tx:
            return self._send(200, {'success': True, 'paid': True,
                                    'transaction': tx})
        return self._send(200, {'success': True, 'paid': False})

    def _transactions(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        hours = int((qs.get('hours') or ['24'])[0])
        now = time.time()
        txs = client.fetch_transactions(_iso(now - hours * 3600),
                                        _iso(now), size=50)
        return self._send(200, {'success': True, 'data': txs})

    def _token_status(self):
        sess = client.session or {}
        if not sess.get('access_token'):
            return self._send(200, {'success': True, 'valid': False,
                                    'error': 'belum login'})
        cfg = client.get_user_config()
        merchant = cfg.get('merchant') or {}
        return self._send(200, {
            'success': True, 'valid': True,
            'merchant_id': merchant.get('id'),
            'outlet_name': merchant.get('name'),
            'token_expires_at': sess.get('expires_at'),
        })

    def _qr_png(self, qris_id):
        with _lock:
            p = state['pending'].get(qris_id)
        if not p:
            return self._send(404, {'success': False,
                                    'error': 'qris tidak ditemukan'})
        png = None
        try:
            import segno
            qr = segno.make(p['qris_code'], error='m')
            import io
            buf = io.BytesIO()
            qr.save(buf, kind='png', scale=8, border=2)
            png = buf.getvalue()
        except ImportError:
            try:
                import qrcode
                import io
                img = qrcode.make(p['qris_code'])
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                png = buf.getvalue()
            except ImportError:
                png = None
        if png is None:
            return self._send(501, {
                'success': False,
                'error': 'library QR belum terpasang (segno/qrcode); '
                         'render qris_code di sisi klien'})
        return self._send(200, png, ctype='image/png')


def main():
    if not API_KEY:
        print('PERINGATAN: GOPAY_GW_API_KEY kosong — endpoint ber-API-key '
              'akan selalu 401.')
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print('gopay-gateway-py berjalan di 127.0.0.1:%d' % PORT, flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
