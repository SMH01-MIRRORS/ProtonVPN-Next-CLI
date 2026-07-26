import os
import unittest
from unittest import mock

from pvpn_cli import ca_bundle


class EnsureCaBundleTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("SSL_CERT_FILE", None)
        os.environ.pop("SSL_CERT_DIR", None)

    def test_working_default_store_is_left_alone(self):
        with mock.patch.object(ca_bundle, "_default_store_is_usable", return_value=True):
            ca_bundle.ensure_ca_bundle()
        self.assertIsNone(os.environ.get("SSL_CERT_FILE"))

    def test_explicit_override_is_never_replaced(self):
        os.environ["SSL_CERT_FILE"] = "/custom/override.pem"
        with mock.patch.object(ca_bundle, "_default_store_is_usable", return_value=False):
            ca_bundle.ensure_ca_bundle()
        self.assertEqual("/custom/override.pem", os.environ["SSL_CERT_FILE"])

    def test_empty_store_falls_back_to_the_system_bundle(self):
        # A Debian-built binary on Arch: OPENSSLDIR points at a directory that
        # does not exist, so nothing is trusted until we redirect OpenSSL.
        with mock.patch.object(ca_bundle, "_default_store_is_usable", return_value=False), \
             mock.patch.object(ca_bundle, "_SYSTEM_CA_BUNDLES", ("/missing/bundle.crt", "/etc/ssl/certs/ca-certificates.crt")), \
             mock.patch.object(os.path, "isfile", lambda path: path == "/etc/ssl/certs/ca-certificates.crt"):
            ca_bundle.ensure_ca_bundle()
        self.assertEqual("/etc/ssl/certs/ca-certificates.crt", os.environ["SSL_CERT_FILE"])

    def test_certifi_is_used_when_no_system_bundle_exists(self):
        with mock.patch.object(ca_bundle, "_default_store_is_usable", return_value=False), \
             mock.patch.object(ca_bundle, "_SYSTEM_CA_BUNDLES", ()), \
             mock.patch.object(ca_bundle, "_bundled_ca_bundle", return_value="/bundle/certifi/cacert.pem"):
            ca_bundle.ensure_ca_bundle()
        self.assertEqual("/bundle/certifi/cacert.pem", os.environ["SSL_CERT_FILE"])

    def test_missing_trust_store_everywhere_is_not_fatal(self):
        with mock.patch.object(ca_bundle, "_default_store_is_usable", return_value=False), \
             mock.patch.object(ca_bundle, "_SYSTEM_CA_BUNDLES", ()), \
             mock.patch.object(ca_bundle, "_bundled_ca_bundle", return_value=None):
            ca_bundle.ensure_ca_bundle()
        self.assertIsNone(os.environ.get("SSL_CERT_FILE"))


if __name__ == "__main__":
    unittest.main()
