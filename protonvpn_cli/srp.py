import hashlib
import bcrypt
import base64
import random

BCRYPT_ALPHABET = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

def encode_bcrypt_base64(data: bytes) -> str:
    sb = []
    i = 0
    length = len(data)
    while i < length:
        b1 = data[i] & 0xff
        i += 1
        sb.append(BCRYPT_ALPHABET[b1 >> 2])
        b2 = 0
        if i < length:
            b2 = data[i] & 0xff
            i += 1
            sb.append(BCRYPT_ALPHABET[((b1 << 4) | (b2 >> 4)) & 0x3f])
        else:
            sb.append(BCRYPT_ALPHABET[(b1 << 4) & 0x3f])
            break
        
        b3 = 0
        if i < length:
            b3 = data[i] & 0xff
            i += 1
            sb.append(BCRYPT_ALPHABET[((b2 << 2) | (b3 >> 6)) & 0x3f])
            sb.append(BCRYPT_ALPHABET[b3 & 0x3f])
        else:
            sb.append(BCRYPT_ALPHABET[(b2 << 2) & 0x3f])
            break
    return "".join(sb)

class SrpHasher:
    @staticmethod
    def concat(*args: bytes) -> bytes:
        return b''.join(args)

    @staticmethod
    def expand_hash(data: bytes) -> bytes:
        result = bytearray(256)
        for i in range(4):
            digest = hashlib.sha512()
            digest.update(data)
            digest.update(bytes([i]))
            h = digest.digest()
            result[i*64 : i*64 + 64] = h
        return bytes(result)

    @staticmethod
    def hash_password_version3(password: bytes, salt: bytes, modulus: bytes) -> bytes:
        salt_with_proton = salt + b"proton"
        
        # Proton uses a custom bcrypt salt encoding logic.
        # They take append(salt, "proton"), base64 encode it with a custom alphabet, 
        # and then pass the string to bcrypt.
        # We construct a 22-char salt string and pass it to bcrypt as $2y$10$...
        encoded_salt_22 = encode_bcrypt_base64(salt_with_proton)[:22]
        
        bcrypt_salt = f"$2y$10${encoded_salt_22}".encode('ascii')
        
        # We hash the password using this exact salt
        full_hashed = bcrypt.hashpw(password, bcrypt_salt)
        
        return SrpHasher.expand_hash(SrpHasher.concat(full_hashed, modulus))


class SrpClient:
    GENERATOR = 2
    BIT_LENGTH = 2048

    def __init__(self, modulus: bytes, hashed_password: bytes, server_ephemeral: bytes):
        self.modulus = modulus
        self.hashed_password = hashed_password
        self.server_ephemeral = server_ephemeral

    @staticmethod
    def verify_and_extract_modulus(signed_modulus: str) -> bytes:
        # Proton's modulus is returned as a PGP Cleartext Signed Message.
        try:
            lines = [l.strip() for l in signed_modulus.splitlines()]
            body_start = -1
            for i, line in enumerate(lines):
                if line.startswith("-----BEGIN PGP SIGNED MESSAGE-----"):
                    continue
                if not line:
                    body_start = i + 1
                    break
            
            sig_start = -1
            for i, line in enumerate(lines):
                if line.startswith("-----BEGIN PGP SIGNATURE-----"):
                    sig_start = i
                    break
            
            if body_start == -1 or sig_start == -1 or body_start >= sig_start:
                return base64.b64decode(signed_modulus.strip())
            
            b64_lines = [l for l in lines[body_start:sig_start] if l]
            return base64.b64decode("".join(b64_lines))
        except Exception:
            return base64.b64decode(signed_modulus.strip())

    @staticmethod
    def to_bigint(data: bytes) -> int:
        return int.from_bytes(data, byteorder='little')

    @staticmethod
    def from_bigint(num: int, length: int) -> bytes:
        # Get Big-Endian bytes, pad it to length, and then reverse to Little-Endian
        b = num.to_bytes(length, byteorder='big')
        return bytes(reversed(b))

    def generate_proofs(self) -> dict:
        N = self.to_bigint(self.modulus)
        x = self.to_bigint(self.hashed_password)
        B = self.to_bigint(self.server_ephemeral)
        
        generator_bytes = self.from_bigint(self.GENERATOR, 256)
        modulus_bytes = self.from_bigint(N, 256)
        
        k_hash = SrpHasher.expand_hash(SrpHasher.concat(generator_bytes, modulus_bytes))
        k = self.to_bigint(k_hash) % N
        
        A_int = 0
        a = 0
        A = b""
        
        while True:
            # Generate a 2048 bit random integer
            # In Python, random.getrandbits(2048) works.
            a = random.getrandbits(self.BIT_LENGTH) % (N - 1)
            if a <= self.BIT_LENGTH * 2:
                continue
            
            A_int = pow(self.GENERATOR, a, N)
            A = self.from_bigint(A_int, 256)
            
            u_hash = SrpHasher.expand_hash(SrpHasher.concat(A, self.server_ephemeral))
            u = self.to_bigint(u_hash)
            if u != 0:
                break
        
        # Shared Secret S = (B - k*g^x)^(a + u*x) % N
        exp = (a + (u * x)) % (N - 1)
        base = (B - (k * pow(self.GENERATOR, x, N))) % N
        S_int = pow(base, exp, N)
        S = self.from_bigint(S_int, 256)
        
        # Client Proof M1 = H(A, B, S)
        M1 = SrpHasher.expand_hash(SrpHasher.concat(A, self.server_ephemeral, S))
        
        return {
            "client_ephemeral": base64.b64encode(A).decode('utf-8'),
            "client_proof": base64.b64encode(M1).decode('utf-8')
        }
