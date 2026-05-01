import sys
sys.path.insert(0, "/Users/austin/parallax-skill")

try:
    import parallax_engine.tools
    print("OK: parallax_engine.tools")
except Exception as e:
    print(f"FAIL: parallax_engine.tools -- {e}")

try:
    from parallax_engine.tools.render import render_scene
    print(f"OK: render_scene is {render_scene}")
except Exception as e:
    print(f"FAIL: tools.render -- {e}")

try:
    from parallax_engine.tools.qa import diff_frames, ssim_score
    print(f"OK: diff_frames={diff_frames}, ssim_score={ssim_score}")
except Exception as e:
    print(f"FAIL: tools.qa -- {e}")

print("import check complete")
