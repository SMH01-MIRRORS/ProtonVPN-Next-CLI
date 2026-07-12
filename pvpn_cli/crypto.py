import hashlib
import base64
import sys
import os
import secrets
import hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("[ERROR] 'cryptography' library is required.")
    sys.exit(1)

class ProtonCrypto:
    @staticmethod
    def generate_vpn_keys():
        priv = ed25519.Ed25519PrivateKey.generate()
        seed = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        hash512 = bytearray(hashlib.sha512(seed).digest())
        hash512[0] &= 248
        hash512[31] &= 127
        hash512[31] |= 64
        wg_priv_b64 = base64.b64encode(bytes(hash512[:32])).decode('utf-8')
        pub = priv.public_key()
        pem_pub = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode('utf-8')
        return wg_priv_b64, pem_pub

class QuicI1Generator:
    QUIC_SALT = bytes([
        0x38, 0x76, 0x2c, 0xf7, 0xf5, 0x59, 0x34, 0xb3, 0x4d, 0x17,
        0x9a, 0xe6, 0xa4, 0xc8, 0x0c, 0xad, 0xcc, 0xbb, 0x7f, 0x0a
    ])

    @staticmethod
    def generate_i1(domain: str) -> str:
        dcid = secrets.token_bytes(1)
        scid = b""
        token = b""
        pkn = b"\x00"
        client_hello = QuicI1Generator._quic_tls_client_hello_sni(domain)
        payload = QuicI1Generator._quic_crypto_frame(client_hello)
        packet = QuicI1Generator._quic_initial(dcid, scid, token, pkn, payload, 0)
        return f"<b 0x{packet.hex()}>"

    @staticmethod
    def _quic_tls_client_hello_sni(sni: str) -> bytes:
        random_bytes = secrets.token_bytes(32)
        sni_bytes = sni.encode('utf-8')
        sni_len = len(sni_bytes)

        # SNI extension
        sni_ext_content = (sni_len + 1).to_bytes(2, 'big') + b"\x00" + sni_len.to_bytes(2, 'big') + sni_bytes
        sni_ext = b"\x00\x00" + len(sni_ext_content).to_bytes(2, 'big') + sni_ext_content

        extensions = (len(sni_ext)).to_bytes(2, 'big') + sni_ext
        hello_body = b"\x03\x03" + random_bytes + b"\x00\x00\x00\x00" + extensions

        final_hello = b"\x01" + len(hello_body).to_bytes(3, 'big') + hello_body
        return final_hello

    @staticmethod
    def _quic_crypto_frame(data: bytes, offset: int = 0) -> bytes:
        offset_var = QuicI1Generator._quic_varint(offset)
        len_var = QuicI1Generator._quic_varint(len(data))
        return b"\x06" + offset_var + len_var + data

    @staticmethod
    def _quic_varint(val: int) -> bytes:
        if val < 0x40: return val.to_bytes(1, 'big')
        if val < 0x4000: return (val | 0x4000).to_bytes(2, 'big')
        if val < 0x40000000: return (val | 0x80000000).to_bytes(4, 'big')
        return (val | (0xC0 << 56)).to_bytes(8, 'big')

    @staticmethod
    def _quic_initial(dcid, scid, token, pkn, payload, padto) -> bytes:
        pkn_len = len(pkn)
        tag_len = 16

        def get_len_var(pad):
            return QuicI1Generator._quic_varint(pkn_len + len(payload) + pad + tag_len)

        padding_len = 0
        if pad_needed := (padto - (8 + len(dcid) + len(scid) + len(token) + pkn_len + len(get_len_var(0)) + len(payload) + tag_len)) > 0:
            padding_len = pad_needed

        if (pkn_len + len(payload) + padding_len + tag_len) < 20:
            padding_len = 20 - pkn_len - len(payload) - tag_len

        len_var = get_len_var(padding_len)
        header = (0xC0 | (pkn_len - 1)).to_bytes(1, 'big') + \
                 b"\x00\x00\x00\x01" + \
                 len(dcid).to_bytes(1, 'big') + dcid + \
                 len(scid).to_bytes(1, 'big') + scid + \
                 len(token).to_bytes(1, 'big') + token + \
                 len_var + pkn

        init_secret = hmac.new(QuicI1Generator.QUIC_SALT, dcid, hashlib.sha256).digest()
        client_secret = QuicI1Generator._quic_derive_label(init_secret, 32, "client in")
        quic_key = QuicI1Generator._quic_derive_label(client_secret, 16, "quic key")
        quic_iv = QuicI1Generator._quic_derive_label(client_secret, 12, "quic iv")
        quic_hp = QuicI1Generator._quic_derive_label(client_secret, 16, "quic hp")

        iv_int = int.from_bytes(quic_iv, 'big')
        pkn_int = int.from_bytes(pkn, 'big')
        nonce = (iv_int ^ pkn_int).to_bytes(12, 'big')

        padded_payload = payload + b"\x00" * padding_len
        aead = AESGCM(quic_key)
        encrypted_payload = aead.encrypt(nonce, padded_payload, header)

        # Header Protection
        sample = encrypted_payload[4-pkn_len : 20-pkn_len]
        cipher = Cipher(algorithms.AES(quic_hp), modes.ECB(), backend=default_backend())
        encryptor = cipher.encryptor()
        mask = encryptor.update(sample) + encryptor.finalize()

        h = bytearray(header)
        h[0] ^= (mask[0] & 0x0F)
        pkn_offset = len(header) - pkn_len
        for i in range(pkn_len):
            h[pkn_offset + i] ^= mask[1 + i]

        return bytes(h) + encrypted_payload

    @staticmethod
    def _quic_derive_label(key, length, label) -> bytes:
        lbl = b"tls13 " + label.encode('utf-8')
        info = length.to_bytes(2, 'big') + len(lbl).to_bytes(1, 'big') + lbl + b"\x00\x01"
        return hmac.new(key, info, hashlib.sha256).digest()[:length]
