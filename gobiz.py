# -*- coding: utf-8 -*-
"""Klien sesi GoBiz (GoPay Merchant) — implementasi sendiri dari protokol
GoID yang terdokumentasi publik (alur portal GoFood Merchant):

- Minta OTP : POST https://api.gobiz.co.id/goid/login/request
              {client_id, phone_number (nasional polos), country_code}
- Tukar OTP : POST https://api.gobiz.co.id/goid/token
              {client_id, grant_type: "otp", data: {otp, otp_token}}
- Refresh   : POST yang sama, grant_type: "refresh_token",
              data: {refresh_token, phone_number, country_code}
              (refresh token BERPUTAR — selalu simpan hasil terbaru)
- Profil    : GET  https://api.gobiz.co.id/goresto/v5/public/users/config
- Mutasi    : GET  https://api.gojekapi.com/merchant-analytics/v2/merchants/transactions
              (gross_amount dalam SEN — dibagi 100 menjadi rupiah)

Sesi disimpan sebagai JSON lokal (izin 0600). Tidak ada data yang dikirim
ke mana pun selain host Gojek/GoBiz di atas.
"""
import json
import os
import time
import urllib.request
import urllib.error
import uuid

GOID_BASE = 'https://api.gobiz.co.id'
ANALYTICS_URL = ('https://api.gojekapi.com/merchant-analytics/v2/'
                 'merchants/transactions')
CLIENT_ID = 'go-biz-web-new'
APP_VERSION = os.environ.get('GOBIZ_APP_VERSION',
                             'platform-v3.125.0-e1923971')
REFRESH_BUFFER_SECONDS = 300
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/148.0.0.0 Safari/537.36')


class GoBizError(Exception):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def normalize_phone(raw: str) -> str:
    """'0812…' / '+62812…' / '62812…' -> '812…' (nasional polos)."""
    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    if digits.startswith('62'):
        digits = digits[2:]
    if digits.startswith('0'):
        digits = digits[1:]
    if not digits.startswith('8') or not (9 <= len(digits) <= 14):
        raise ValueError('nomor HP tidak valid: %r' % raw)
    return digits


def _http_json(method, url, body=None, headers=None, timeout=60):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        hdrs['content-type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=hdrs,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {'raw': raw}
        msg = None
        errs = payload.get('errors') if isinstance(payload, dict) else None
        if errs and isinstance(errs, list) and errs and \
                isinstance(errs[0], dict):
            msg = errs[0].get('message')
        raise GoBizError(msg or ('HTTP %d dari GoBiz' % e.code),
                         status=e.code, payload=payload)
    except urllib.error.URLError as e:
        raise GoBizError('gagal menghubungi GoBiz: %s' % e.reason)


class SessionStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def save(self, session):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)),
                    exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(session, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def clear(self):
        try:
            os.remove(self.path)
        except OSError:
            pass


class GoBizClient:
    def __init__(self, store: SessionStore):
        self.store = store
        self.session = store.load()

    # ---------- header perangkat (meniru portal web resmi) ----------
    def _device_headers(self, auth=False):
        unique_id = (self.session or {}).get('unique_id')
        if not unique_id:
            unique_id = str(uuid.uuid4())
        hdrs = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'id',
            'authentication-type': 'go-id',
            'gojek-country-code': 'ID',
            'gojek-timezone': 'Asia/Jakarta',
            'origin': 'https://portal.gofoodmerchant.co.id',
            'referer': 'https://portal.gofoodmerchant.co.id/',
            'user-agent': USER_AGENT,
            'x-appid': 'go-biz-web-dashboard',
            'x-appversion': APP_VERSION,
            'x-deviceos': 'Web',
            'x-phonemake': 'Windows 10 64-bit',
            'x-phonemodel': 'Chrome 148.0.0.0 on Windows 10 64-bit',
            'x-platform': 'Web',
            'x-uniqueid': unique_id,
            'x-user-locale': 'en-GB',
            'x-user-type': 'merchant',
        }
        if auth and self.session and self.session.get('access_token'):
            hdrs['Authorization'] = 'Bearer ' + self.session['access_token']
        return hdrs

    # ---------- login OTP ----------
    def request_otp(self, phone_raw):
        phone = normalize_phone(phone_raw)
        unique_id = str(uuid.uuid4())
        self.session = {'unique_id': unique_id, 'phone_number': phone,
                        'country_code': '62'}
        status, payload = _http_json(
            'POST', GOID_BASE + '/goid/login/request',
            body={'client_id': CLIENT_ID, 'phone_number': phone,
                  'country_code': '62'},
            headers=self._device_headers())
        data = payload.get('data') or {}
        if not data.get('otp_token'):
            raise GoBizError('respons OTP tidak memuat otp_token',
                             status=status, payload=payload)
        self.session['otp_token'] = data['otp_token']
        self.store.save(self.session)
        return {'otp_expires_in': data.get('otp_expires_in'),
                'otp_length': data.get('otp_length')}

    def login_with_otp(self, otp_code):
        if not self.session or not self.session.get('otp_token'):
            raise GoBizError('belum ada permintaan OTP — '
                             'jalankan request_otp dulu')
        status, payload = _http_json(
            'POST', GOID_BASE + '/goid/token',
            body={'client_id': CLIENT_ID, 'grant_type': 'otp',
                  'data': {'otp': str(otp_code).strip(),
                           'otp_token': self.session['otp_token']}},
            headers=self._device_headers())
        self._adopt_token_response(payload, status)
        self.session.pop('otp_token', None)
        self.store.save(self.session)
        return self.session

    def _adopt_token_response(self, payload, status):
        data = payload.get('data') or payload
        access = data.get('access_token')
        refresh = data.get('refresh_token')
        if not access:
            raise GoBizError('respons token tidak memuat access_token',
                             status=status, payload=payload)
        self.session = dict(self.session or {})
        self.session['access_token'] = access
        if refresh:
            self.session['refresh_token'] = refresh
        expires_in = data.get('expires_in') or 86400
        self.session['expires_at'] = int(time.time()) + int(expires_in)

    def refresh_session(self):
        if not self.session or not self.session.get('refresh_token'):
            raise GoBizError('tidak ada refresh_token untuk disegarkan')
        status, payload = _http_json(
            'POST', GOID_BASE + '/goid/token',
            body={'client_id': CLIENT_ID, 'grant_type': 'refresh_token',
                  'data': {
                      'refresh_token': self.session['refresh_token'],
                      'phone_number': self.session.get('phone_number'),
                      'country_code': self.session.get('country_code',
                                                       '62')}},
            headers=self._device_headers())
        self._adopt_token_response(payload, status)
        self.store.save(self.session)
        return self.session

    # ---------- sesi valid (refresh proaktif + sekali ulang saat 401) --
    def ensure_session(self):
        if not self.session or not self.session.get('access_token'):
            raise GoBizError('belum login — jalankan login_cli.py dulu')
        exp = int(self.session.get('expires_at') or 0)
        if exp - int(time.time()) < REFRESH_BUFFER_SECONDS and \
                self.session.get('refresh_token'):
            self.refresh_session()
        return self.session

    def _authed_get(self, url, params=None):
        self.ensure_session()
        full = url
        if params:
            from urllib.parse import urlencode
            full = url + '?' + urlencode(params)
        try:
            return _http_json('GET', full,
                              headers=self._device_headers(auth=True))
        except GoBizError as e:
            if e.status == 401 and self.session.get('refresh_token'):
                self.refresh_session()
                return _http_json('GET', full,
                                  headers=self._device_headers(auth=True))
            raise

    # ---------- data merchant & mutasi ----------
    def get_user_config(self):
        _, payload = self._authed_get(
            GOID_BASE + '/goresto/v5/public/users/config')
        data = payload.get('data') or payload
        merchant = data.get('merchant') or {}
        if merchant.get('id'):
            self.session['merchant_id'] = merchant['id']
            self.session['outlet_name'] = merchant.get('name')
            self.store.save(self.session)
        return data

    def merchant_id(self):
        if self.session and self.session.get('merchant_id'):
            return self.session['merchant_id']
        self.get_user_config()
        return (self.session or {}).get('merchant_id')

    def fetch_transactions(self, start_iso, end_iso, size=30,
                           merchant_id=None):
        """Ambil mutasi settlement; nominal dinormalkan ke rupiah."""
        mid = merchant_id or self.merchant_id() or ''
        _, payload = self._authed_get(ANALYTICS_URL, params={
            'from': 0,
            'size': size,
            'statuses': 'SETTLEMENT,CAPTURE,REFUND,PARTIAL_REFUND',
            'payment_types': 'QRIS,GOPAY,OFFLINE_CREDIT_CARD,'
                             'OFFLINE_DEBIT_CARD,CREDIT_CARD',
            'start_time': start_iso,
            'end_time': end_iso,
            'merchant_ids': mid,
        })
        data = payload.get('data') or payload
        raw_list = data.get('transactions') or []
        out = []
        for tx in raw_list:
            gross = tx.get('gross_amount')
            if gross is None:
                gross = tx.get('real_gross_amount') or 0
            sen = int(round(float(gross)))
            out.append({
                'transaction_id': tx.get('id') or tx.get('order_id'),
                'order_id': tx.get('order_id'),
                'amount_sen': sen,
                'amount': sen // 100,
                'status': (tx.get('transaction_status') or '').lower(),
                'time': tx.get('transaction_time') or
                        tx.get('settlement_time'),
                'issuer': tx.get('qris_provider_aspi_issuer') or
                          'GoPay / Bank',
                'payment_type': tx.get('payment_type'),
            })
        return out
