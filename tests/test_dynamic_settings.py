import io
import os
from unittest import mock

import paramiko
import pytest

from redash.settings.dynamic_settings import _private_key_from_env, ssh_tunnel_auth


def _rsa_private_key_pem(password=None):
    """Generate an in-memory RSA private key, returning (PEM text, public base64)."""
    key = paramiko.RSAKey.generate(2048)
    buffer = io.StringIO()
    key.write_private_key(buffer, password=password)
    return buffer.getvalue(), key.get_base64()


def test_ssh_tunnel_auth_has_no_pkey_when_env_unset():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REDASH_SSH_TUNNEL_PRIVATE_KEY", None)
        auth = ssh_tunnel_auth()
    assert "ssh_pkey" not in auth


def test_ssh_tunnel_auth_loads_key_from_env():
    pem, public_base64 = _rsa_private_key_pem()
    with mock.patch.dict(os.environ, {"REDASH_SSH_TUNNEL_PRIVATE_KEY": pem}):
        auth = ssh_tunnel_auth()
    assert isinstance(auth["ssh_pkey"], paramiko.PKey)
    assert auth["ssh_pkey"].get_base64() == public_base64


def test_loads_passphrase_protected_key():
    pem, public_base64 = _rsa_private_key_pem(password="s3cret")
    env = {
        "REDASH_SSH_TUNNEL_PRIVATE_KEY": pem,
        "REDASH_SSH_TUNNEL_PRIVATE_KEY_PASSWORD": "s3cret",
    }
    with mock.patch.dict(os.environ, env):
        key = _private_key_from_env()
    assert key.get_base64() == public_base64


def test_invalid_key_raises_sshexception():
    with mock.patch.dict(os.environ, {"REDASH_SSH_TUNNEL_PRIVATE_KEY": "-----not a real key-----"}):
        with pytest.raises(paramiko.SSHException):
            _private_key_from_env()


def test_non_sshexception_loader_error_is_wrapped():
    # paramiko can leak non-SSHException errors (e.g. UnicodeDecodeError, a ValueError
    # subclass, on a corrupted OpenSSH key). Those must be caught and re-raised as a clear
    # SSHException, not propagated raw into the SSH tunnel setup.
    with mock.patch.dict(os.environ, {"REDASH_SSH_TUNNEL_PRIVATE_KEY": "anything"}):
        with (
            mock.patch.object(paramiko.Ed25519Key, "from_private_key", side_effect=ValueError("boom")),
            mock.patch.object(paramiko.ECDSAKey, "from_private_key", side_effect=ValueError("boom")),
            mock.patch.object(paramiko.RSAKey, "from_private_key", side_effect=ValueError("boom")),
        ):
            with pytest.raises(paramiko.SSHException):
                _private_key_from_env()
