# -*- coding: utf-8 -*-
"""Login GoBiz sekali jalan: nomor HP -> OTP SMS -> sesi tersimpan.

Jalankan:  python3 login_cli.py
Token TIDAK pernah dicetak; yang tampil hanya nama merchant & masa berlaku.
"""
import os
import sys
import time

from gobiz import GoBizClient, SessionStore, GoBizError

BASE = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.environ.get(
    'GOPAY_GW_SESSION', os.path.join(BASE, 'data', 'session.json'))


def main():
    client = GoBizClient(SessionStore(SESSION_PATH))
    phone = input('Nomor HP GoBiz (mis. 0812xxxxxxx): ').strip()
    try:
        info = client.request_otp(phone)
    except (GoBizError, ValueError) as e:
        print('Gagal meminta OTP:', e)
        return 1
    print('OTP terkirim via SMS/WA (berlaku ±%s detik, %s digit).'
          % (info.get('otp_expires_in'), info.get('otp_length')))
    code = input('Masukkan kode OTP: ').strip()
    try:
        session = client.login_with_otp(code)
        cfg = client.get_user_config()
    except GoBizError as e:
        print('Login gagal:', e)
        return 1
    merchant = cfg.get('merchant') or {}
    sisa = int(session.get('expires_at', 0)) - int(time.time())
    print('LOGIN BERHASIL')
    print('  Merchant : %s (%s)' % (merchant.get('name'),
                                    merchant.get('id')))
    print('  Sesi     : tersimpan di %s' % SESSION_PATH)
    print('  Token    : berlaku ±%d jam, refresh otomatis' % (sisa // 3600))
    return 0


if __name__ == '__main__':
    sys.exit(main())
