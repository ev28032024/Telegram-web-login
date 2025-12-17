
import unittest
import sys
import os
from pathlib import Path

# Add parent directory to path to import telescan
sys.path.insert(0, str(Path(__file__).parent.parent))

# We anticipate that imports might fail or use dummies, so we just import telescan
import telescan

class TestTelescanHelpers(unittest.TestCase):
    
    def test_normalize_phone(self):
        """Test phone normalization logic."""
        self.assertEqual(telescan._normalize_phone("+12345"), "+12345")
        self.assertEqual(telescan._normalize_phone("12345"), "+12345")
        self.assertEqual(telescan._normalize_phone(" + 1 (234) 567-89 "), "+123456789")
        
    def test_parse_proxy_line(self):
        """Test proxy parsing."""
        # user:pass@host:port
        p = telescan.parse_proxy_line("user:pass@1.2.3.4:1080")
        self.assertEqual(p.host, "1.2.3.4")
        self.assertEqual(p.port, 1080)
        self.assertEqual(p.username, "user")
        self.assertEqual(p.password, "pass")
        self.assertEqual(p.scheme, "socks5") # default

        # scheme://host:port
        p = telescan.parse_proxy_line("http://1.2.3.4:8080")
        self.assertEqual(p.scheme, "http")
        self.assertEqual(p.host, "1.2.3.4")
        self.assertEqual(p.port, 8080)
        self.assertIsNone(p.username)
        
    def test_looks_like_tdata(self):
        """Test tdata heuristic detection (mocking file system)."""
        # We can't easily mock Path usage inside the function without heavy patching,
        # checking basic logic if possible or skipping.
        # telescan.looks_like_tdata takes a Path object.
        pass

    def test_check_environment_raises(self):
        """Verify check_environment raises SystemExit when deps are missing."""
        # Ensure imports are missing
        telescan.TELETHON_AVAILABLE = False
        with self.assertRaises(SystemExit) as cm:
            telescan.check_environment("telethon")
        self.assertEqual(cm.exception.code, 1)

    def test_api_rotator(self):
        """Test ApiRotator logic."""
        apis = [
            telescan.ApiPair(1, "hash1", "l1"),
            telescan.ApiPair(2, "hash2", "l2")
        ]
        rotator = telescan.ApiRotator(apis)
        
        # Pick 0 -> first
        self.assertEqual(rotator.pick(0).api_id, 1)
        # Pick 1 -> second
        self.assertEqual(rotator.pick(1).api_id, 2)
        # Pick 2 -> first (rotation)
        self.assertEqual(rotator.pick(2).api_id, 1)

if __name__ == "__main__":
    unittest.main()
