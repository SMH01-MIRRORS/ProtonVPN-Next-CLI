import socket
import unittest
from unittest import mock

from pvpn_cli import doh


class DoHResolverTest(unittest.TestCase):
    def test_public_hostname_uses_doh_resolver(self):
        expected = socket.getaddrinfo("203.0.113.10", 443, socket.AF_INET, socket.SOCK_STREAM)
        with mock.patch.object(doh._RESOLVER, "resolve", return_value=["203.0.113.10"]) as resolve:
            actual = doh._secure_getaddrinfo(
                "vpn-api.proton.me",
                443,
                socket.AF_INET,
                socket.SOCK_STREAM,
            )
        resolve.assert_called_once_with("vpn-api.proton.me", socket.AF_INET)
        self.assertEqual(expected, actual)

    def test_numeric_bootstrap_address_bypasses_recursive_doh(self):
        with mock.patch.object(doh._RESOLVER, "resolve") as resolve:
            result = doh._secure_getaddrinfo("1.1.1.1", 443, socket.AF_INET, socket.SOCK_STREAM)
        resolve.assert_not_called()
        self.assertTrue(result)

    def test_resolution_fails_closed_without_plain_dns_fallback(self):
        error = doh.DoHResolutionError(socket.EAI_AGAIN, "DoH unavailable")
        with mock.patch.object(doh._RESOLVER, "resolve", side_effect=error):
            with self.assertRaises(doh.DoHResolutionError):
                doh._secure_getaddrinfo(
                    "vpn-api.proton.me",
                    443,
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                )

    def test_installation_is_mandatory_and_idempotent(self):
        doh.install_doh()
        first = socket.getaddrinfo
        doh.install_doh()
        self.assertIs(first, socket.getaddrinfo)
        self.assertIs(socket.getaddrinfo, doh._secure_getaddrinfo)


if __name__ == "__main__":
    unittest.main()
