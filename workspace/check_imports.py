import sys
mods = ["skimage", "cv2", "PIL", "numpy"]
for m in mods:
    try:
        __import__(m)
        print(f"OK: {m}")
    except ImportError as e:
        print(f"MISSING: {m} -- {e}")
