import sys
mods = ["anthropic", "skimage", "cv2", "PIL", "numpy", "scipy"]
for m in mods:
    try:
        mod = __import__(m)
        ver = getattr(mod, "__version__", "?")
        print(f"OK: {m}=={ver}")
    except ImportError as e:
        print(f"MISSING: {m} -- {e}")
