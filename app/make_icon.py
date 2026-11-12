"""Draws the app icon (1024px PNG) with OpenCV: a warm film frame with a score arc and a spark."""
import math
import sys

import cv2
import numpy as np

S = 1024
SS = 4  # supersample for smooth edges
W = S * SS


def rounded_mask(w, r):
    m = np.zeros((w, w), np.uint8)
    cv2.rectangle(m, (r, 0), (w - r, w), 255, -1)
    cv2.rectangle(m, (0, r), (w, w - r), 255, -1)
    for cx, cy in ((r, r), (w - r, r), (r, w - r), (w - r, w - r)):
        cv2.circle(m, (cx, cy), r, 255, -1, cv2.LINE_AA)
    return m


img = np.zeros((W, W, 4), np.uint8)
inset = int(W * 0.09)
size = W - 2 * inset
# background gradient: deep charcoal to warm brown
yy = np.linspace(0, 1, size)[:, None]
top = np.array([38, 30, 28], np.float32)  # BGR
bot = np.array([22, 40, 72], np.float32)
grad = (top * (1 - yy) + bot * yy)[:, :, None].repeat(1, 2).transpose(0, 2, 1)
grad = np.broadcast_to(grad.reshape(size, 1, 3), (size, size, 3))
mask = rounded_mask(size, int(size * 0.225))
tile = np.dstack([grad.astype(np.uint8), mask])
img[inset:inset + size, inset:inset + size] = tile

c = W // 2
orange = (40, 120, 245, 255)
cream = (225, 238, 248, 255)
# film frame
fw, fh = int(size * 0.62), int(size * 0.40)
x0, y0 = c - fw // 2, c - fh // 2 + int(size * 0.04)
cv2.rectangle(img, (x0, y0), (x0 + fw, y0 + fh), cream, int(size * 0.022), cv2.LINE_AA)
hole = int(size * 0.035)
for i in range(7):
    hx = x0 + int(fw * (0.08 + i * 0.14))
    for hy in (y0 - int(size * 0.06), y0 + fh + int(size * 0.025)):
        cv2.rectangle(img, (hx, hy), (hx + hole, hy + hole), cream, -1, cv2.LINE_AA)
# score arc
r = int(size * 0.13)
center = (c, y0 + fh // 2)
cv2.ellipse(img, center, (r, r), 0, 0, 360, (90, 90, 90, 255), int(size * 0.03), cv2.LINE_AA)
cv2.ellipse(img, center, (r, r), -90, 0, 290, orange, int(size * 0.03), cv2.LINE_AA)
# spark
sx, sy, sr = x0 + fw - int(size * 0.02), y0 - int(size * 0.02), int(size * 0.09)
pts = []
for k in range(8):
    ang = k * math.pi / 4 - math.pi / 2
    rad = sr if k % 2 == 0 else sr * 0.28
    pts.append((int(sx + rad * math.cos(ang)), int(sy + rad * math.sin(ang))))
cv2.fillPoly(img, [np.array(pts, np.int32)], orange, cv2.LINE_AA)

out = cv2.resize(img, (S, S), interpolation=cv2.INTER_AREA)
cv2.imwrite(sys.argv[1], out)
