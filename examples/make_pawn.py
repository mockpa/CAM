"""Writes examples/pawn.stl: a 48 mm chess pawn lying on the X axis (base at X0).

python examples/make_pawn.py
"""
import math
from pathlib import Path

from camcore.stl import revolve

profile = [(0, 13.0), (3, 13.0), (4, 12.6), (5, 11.6), (6, 10.4), (10, 9.0), (16, 7.6), (22, 6.6),
           (26, 6.2), (27, 8.6), (28.5, 9.2), (30, 8.6), (31, 5.6), (32.5, 5.2)]
# head: sphere of radius 8 centred at X40, from where it meets the neck to near the top
for k in range(23):
    th = math.radians(140 - k * 130 / 22)          # 140 deg (neck side) .. 10 deg (top)
    profile.append((40 + 8 * math.cos(th), 8 * math.sin(th)))

if __name__ == "__main__":
    out = Path(__file__).with_name("pawn.stl")
    revolve(profile, 96).export(out)
    print(out)
