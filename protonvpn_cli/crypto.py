import hashlib
import base64
import sys

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("[ERROR] 'cryptography' library is required to generate WireGuard keys.")
    print("Please install it: pip install cryptography")
    print("Or on NixOS: nix-shell -p python3Packages.cryptography")
    sys.exit(1)

class ProtonCrypto:
    @staticmethod
    def generate_vpn_keys():
        """
        Generates an Ed25519 key pair, derives the WireGuard (X25519) private key,
        and returns the Ed25519 public key in PEM format for the Proton API.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        seed = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        
        # Derive X25519 private key
        hash512 = bytearray(hashlib.sha512(seed).digest())
        hash512[0] &= 248
        hash512[31] &= 127
        hash512[31] |= 64
        wg_priv_b64 = base64.b64encode(bytes(hash512[:32])).decode('utf-8')
        
        # Get Ed25519 public key as PEM
        pub = priv.public_key()
        pem_pub = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode('utf-8')
        
        return wg_priv_b64, pem_pub
