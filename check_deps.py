
import sys

def check_import(name):
    try:
        __import__(name)
        print(f"[OK] {name}")
    except ImportError as e:
        print(f"[MISSING] {name}: {e}")

print(f"Python: {sys.version}")
check_import("telethon")
check_import("opentele")
check_import("tqdm")
check_import("socks")
check_import("playwright")
check_import("tomllib") # or tomli
