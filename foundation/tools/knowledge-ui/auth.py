#!/usr/bin/env python3
"""PBKDF2-SHA256 Basic authentication; password input never enters argv/env."""
import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path


def loopback(host):
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


def credentials(env):
    if loopback(env.get('UI_HOST', '127.0.0.1')):
        return None
    try:
        user, encoded = Path(env['KNOWLEDGE_UI_BASIC_AUTH_FILE']).read_text().strip().split(':', 1)
        scheme, count, salt, digest = encoded.split('$')
        iterations = int(count)
        salt, digest = bytes.fromhex(salt), bytes.fromhex(digest)
        if not user or scheme != 'pbkdf2-sha256' or not 100000 <= iterations <= 10000000 or len(salt) != 16 or len(digest) != 32:
            raise ValueError()
        return user, iterations, salt, digest
    except (KeyError, OSError, ValueError):
        raise ValueError('Non-loopback UI requires a readable, valid KNOWLEDGE_UI_BASIC_AUTH_FILE') from None


def authorized(header, credential):
    try:
        scheme, payload = header.split(' ', 1)
        if scheme.lower() != 'basic':
            return False
        user, password = base64.b64decode(payload, validate=True).decode('utf-8').split(':', 1)
    except (ValueError, UnicodeError, binascii.Error):
        return False
    expected_user, count, salt, digest = credential
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, count)
    return hmac.compare_digest(user.encode(), expected_user.encode()) & hmac.compare_digest(actual, digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('user')
    args = parser.parse_args()
    if not args.user or ':' in args.user or '\n' in args.user:
        parser.error('Invalid username')
    password = getpass.getpass('Password: ')
    if not password or password != getpass.getpass('Confirm password: '):
        parser.error('Passwords must match and be nonempty')
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
    with os.fdopen(os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(f'{args.user}:pbkdf2-sha256$600000${salt.hex()}${digest.hex()}\n')


if __name__ == '__main__':
    main()
