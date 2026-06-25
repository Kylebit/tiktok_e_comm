import subprocess
from PIL import Image, ImageStat

def download(url, path):
    subprocess.run(["curl", "-s", "--noproxy", "*", "-o", path, url], check=True)

def _pad_piece(strip, pad_h, target_w, mean, std_threshold=18):
    std = ImageStat.Stat(strip).stddev
    if max(std) < std_threshold:
        return Image.new("RGB", (target_w, pad_h), tuple(int(x) for x in mean))
    flipped = strip.transpose(Image.FLIP_TOP_BOTTOM)
    return flipped.resize((target_w, pad_h))

def to_3x4(src_path, dst_path, target_w=1200, target_h=1600):
    im = Image.open(src_path).convert("RGB")
    scale = target_w / im.width
    fg_w, fg_h = target_w, int(im.height * scale)
    if fg_h > target_h:
        scale = target_h / im.height
        fg_w, fg_h = int(im.width * scale), target_h
    fg = im.resize((fg_w, fg_h))

    pad_total = target_h - fg_h
    top_pad = pad_total // 2
    bottom_pad = pad_total - top_pad

    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    x = (target_w - fg_w) // 2

    strip_h = max(1, min(60, fg_h // 4))
    if top_pad > 0:
        top_strip = fg.crop((0, 0, fg_w, strip_h))
        mean = ImageStat.Stat(top_strip).mean
        top_piece = _pad_piece(top_strip, top_pad, fg_w, mean)
        canvas.paste(top_piece, (x, 0))
    if bottom_pad > 0:
        bottom_strip = fg.crop((0, fg_h - strip_h, fg_w, fg_h))
        mean = ImageStat.Stat(bottom_strip).mean
        bottom_piece = _pad_piece(bottom_strip, bottom_pad, fg_w, mean)
        canvas.paste(bottom_piece, (x, target_h - bottom_pad))

    canvas.paste(fg, (x, top_pad))
    canvas.save(dst_path, "JPEG", quality=92)

def upload(path):
    cmd = ["curl", "-s", "--noproxy", "*", "-F", "source=@" + path,
           "https://freeimage.host/api/1/upload?key=6d207e02198a847aa98d0a2a901485a5&format=json"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    import json
    d = json.loads(out)
    return d["image"]["url"]

if __name__ == "__main__":
    import sys
    download(sys.argv[1], "/tmp/_src2.jpg")
    to_3x4("/tmp/_src2.jpg", "/tmp/_out2.jpg")
    print("saved /tmp/_out2.jpg")
