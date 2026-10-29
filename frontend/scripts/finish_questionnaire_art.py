"""把 gen_questionnaire_art.py 生成的候选 PNG 定稿为 src/assets/questionnaire-art/*.webp（2026-10-23）。
右图常落在画面中间，被问卷卡片挡住；这里找出建筑主体所在列，把主体平移到外侧边缘（允许裁掉 3% 宽、最多平移 30%），
空出的一侧用原图边缘那一列拉伸补齐，再存 WebP（质量 80）。需要 Pillow。
用法：python3 finish_questionnaire_art.py <PNG 目录> <输出目录>；选用的候选写在 PICK 里。
"""
import os, sys
from PIL import Image

CITIES = "hangzhou nanjing suzhou beijing shanghai guangzhou shenzhen chengdu chongqing wuhan".split()
# 左图用首轮（seed 20261023）；右图用第二轮加了“贴边”提示词后的 seed 32，广州右图首轮已贴边
PICK = {f"{c}-left": f"{c}-left-20261023_00001_.png" for c in CITIES}
PICK.update({f"{c}-right": f"{c}-right-32_00001_.png" for c in CITIES})
PICK["guangzhou-right"] = "guangzhou-right-20261023_00001_.png"


def finish(src: str, side: str) -> Image.Image:
    im = Image.open(src).convert("RGB")
    W, H = im.size
    g = im.convert("L").load()
    # 主体列：该列（忽略最底部 8% 的桌面）有超过 2% 的采样像素明显暗于底色
    rows = range(0, int(H * .92), 4)
    cols = [x for x in range(W) if sum(1 for y in rows if g[x, y] < 200) > len(rows) * .02]
    lo, hi = min(cols), max(cols)
    shift = min(int(W * .30), (W - 1 - hi) + int(W * .03)) if side == "right" else -min(int(W * .30), lo + int(W * .03))
    out = Image.new("RGB", (W, H))
    out.paste(im, (shift, 0))
    if shift > 0:
        out.paste(im.crop((0, 0, 1, H)).resize((shift, H)), (0, 0))
    elif shift < 0:
        out.paste(im.crop((W - 1, 0, W, H)).resize((-shift, H)), (W + shift, 0))
    return out


if __name__ == "__main__":
    src_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    for key, name in PICK.items():
        path = f"{out_dir}/{key}.webp"
        finish(f"{src_dir}/{name}", key.rsplit("-", 1)[1]).save(path, "WEBP", quality=80, method=6)
        print(key, os.path.getsize(path) // 1024, "KB")
