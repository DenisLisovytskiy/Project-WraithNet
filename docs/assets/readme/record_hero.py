"""Record the README chase-camera animation from a replay trace.

    pip install pillow   # only this script needs it
    python docs/assets/readme/record_hero.py path/to/trace.jsonl docs/assets/readme/hero-chase.webp

The viewer runs on a fixed-step virtual clock and every rendered frame is
captured, so slow frame grabs or encoding can never drop or repeat frames
(wall-clock-timed screenshots did: the old GIF had 5 distinct frames in 7 s).
Animated WebP, not GIF: the chase camera changes every pixel each frame, and
a smooth GIF of this clip is ~17 MB versus ~4 MB as WebP.
"""

import sys

from panda3d.core import loadPrcFileData

FPS = 25
loadPrcFileData("", f"win-size 1600 900\nclock-mode non-real-time\nclock-frame-rate {FPS}\nsync-video #f")

from PIL import Image  # noqa: E402

from viewer3d.app import WraithNetViewer3D  # noqa: E402

CHASE_AT_S, START_S, END_S = 5.0, 8.0, 15.0
WIDTH = 960

trace, out = sys.argv[1], sys.argv[2]
app = WraithNetViewer3D(start_client=False, replay_path=trace, playback_rate=1.0)
app.task_mgr.do_method_later(CHASE_AT_S, lambda t: app._on_chase(), "chase")
app.task_mgr.do_method_later(CHASE_AT_S + 0.1, lambda t: app._cycle_selected_jet(), "next")
frames: list[Image.Image] = []


def grab(task):
    t = app.task_mgr.globalClock.get_frame_time()
    if t >= END_S:
        app.userExit()
    if t >= START_S:
        tex = app.win.get_screenshot()
        img = Image.frombytes("RGB", (tex.get_x_size(), tex.get_y_size()), bytes(tex.get_ram_image_as("RGB")))
        size = (WIDTH, round(img.height * WIDTH / img.width))
        frames.append(img.transpose(Image.FLIP_TOP_BOTTOM).resize(size, Image.LANCZOS))
    return task.cont


app.task_mgr.add(grab, "grab", sort=1000)
try:
    app.run()
except SystemExit:
    pass
frames[0].save(out, save_all=True, append_images=frames[1:], duration=1000 // FPS, loop=0, quality=60, method=4)
print(len(frames), "frames")
