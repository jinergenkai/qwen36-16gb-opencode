"""Benchmark Qwen3.6 local vs Claude Code: tốc độ (prefill/decode theo độ sâu context) + task agent tự chấm.

Usage:
  python bench.py q4 orig q5                      # base tasks T1-T5 (+ speed) qua OpenCode
  python bench.py q4v --tasks web,vision          # Q4 với mmproj (vision)
  python bench.py claude --tasks all              # Claude Code headless (claude -p), không cần GPU
Kết quả: bench\\results\\<model>\\result.json + transcript từng task
"""
import json, os, random, re, shutil, subprocess, sys, time, hashlib, urllib.request

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
RESULTS = os.path.join(BENCH, "results")
URL = "http://127.0.0.1:8080"
# Native binary (the npm .cmd/.ps1 shims mangle multi-line prompts). Override with OPENCODE_BIN.
OPENCODE = os.environ.get("OPENCODE_BIN") or os.path.join(
    os.path.dirname(shutil.which("opencode") or ""), "node_modules", "opencode-ai", "bin", "opencode.exe")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    print(line, flush=True)
    with open(os.path.join(RESULTS, "bench.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http(path, body=None, timeout=1800):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def kill_server():
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
    time.sleep(3)


def start_server(model, outdir, vision=False):
    kill_server()
    logf = open(os.path.join(outdir, "server.log"), "w", encoding="utf-8", errors="replace")
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                      os.path.join(ROOT, "run-qwen.ps1"), "-Model", model] + (["-Vision"] if vision else []),
                     stdout=logf, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 600:
        try:
            if http("/health", timeout=3).get("status") == "ok":
                return time.time() - t0
        except Exception:
            pass
        time.sleep(5)
    raise RuntimeError("server không lên được")


def mem_snapshot():
    out = {}
    try:
        v = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True).stdout.strip()
        out["vram_used_mib"] = int(v)
    except Exception:
        pass
    ps = ("$p=Get-Process llama-server -ea 0; $a=(Get-Counter '\\Memory\\Available MBytes').CounterSamples[0].CookedValue;"
          "\"$([math]::Round($p.WorkingSet64/1MB)) $([math]::Round($a))\"")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.split()
    if len(r) == 2:
        out["server_ram_mib"], out["available_ram_mib"] = int(r[0]), int(r[1])
    return out


# ---------------------------------------------------------------- speed

FILLER = ("def handler_{i}(req):\n    # xử lý request số {i}, kiểm tra quyền và ghi log\n"
          "    if req.user is None:\n        raise PermissionError('anonymous {i}')\n"
          "    return {{'id': {i}, 'ok': True, 'items': [x * {i} for x in req.items]}}\n\n")


def make_prompt(n_tokens):
    # ~ 1 khối FILLER ≈ 60 token
    nonce = random.randint(0, 10**9)
    blocks = max(1, n_tokens // 60)
    return f"# run {nonce}\n" + "".join(FILLER.format(i=i) for i in range(blocks))


def speed_test(outdir):
    res = []
    for depth in (4000, 32000, 128000):
        body = {"messages": [{"role": "user", "content": make_prompt(depth) + "\nViết tiếp một đoạn mô tả dài về code trên."}],
                "max_tokens": 256, "ignore_eos": True, "chat_template_kwargs": {"enable_thinking": False}}
        t0 = time.time()
        r = http("/v1/chat/completions", body)
        t = r["timings"]
        row = {"depth": t["prompt_n"], "prefill_tps": round(t["prompt_per_second"], 1),
               "decode_tps": round(t["predicted_per_second"], 1), "wall_s": round(time.time() - t0, 1)}
        row.update(mem_snapshot())
        log(f"  speed @ {row['depth']} tok: prefill {row['prefill_tps']} t/s, decode {row['decode_tps']} t/s")
        res.append(row)
    return res


# ---------------------------------------------------------------- tasks

def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def py(cwd, *args, timeout=60):
    r = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def setup_t1(d):
    w(f"{d}/notes/team.md", "# Team\n- Backend: Hùng\n- Frontend: Lan\n")
    w(f"{d}/notes/deploy.md", "# Deploy\nServer chạy ở cổng 8443.\nMã bí mật deploy là ZEBRA-4471, chỉ dùng cho staging.\n")
    w(f"{d}/notes/todo.md", "# TODO\n- Viết test\n- Mã cũ ALPHA-1000 đã bị thu hồi\n")
    return ("Đọc các file trong thư mục notes và cho biết mã bí mật deploy đang dùng là gì, dùng cho môi trường nào. "
            "Trả lời ngắn gọn.")


def check_t1(d, out):
    return "ZEBRA-4471" in out and "staging" in out.lower(), ""


def setup_t2(d):
    rnd = random.Random(42)
    mods = ["auth", "db", "cache", "api"]
    lines, counts = [], {}
    for i in range(300):
        m = rnd.choice(mods)
        lvl = rnd.choice(["INFO", "INFO", "WARN", "ERROR"])
        msg = rnd.choice(['timeout "upstream"', 'path C:\\tmp\\x not found', "ok", 'bad json {"a":1']).replace('"', '\\"')
        lines.append(f'2026-10-16T10:{i % 60:02d}:00 [{lvl}] module={m} msg="{msg}"')
        if lvl == "ERROR":
            counts[m] = counts.get(m, 0) + 1
    w(f"{d}/app.log", "\n".join(lines) + "\n")
    w(f"{d}/.expected", json.dumps(counts, sort_keys=True))
    return ("Viết file parse_log.py: đọc app.log, dùng regex đếm số dòng có level ERROR theo từng module "
            "(định dạng mỗi dòng: `<time> [LEVEL] module=<name> msg=\"...\"`, msg có thể chứa dấu nháy đã escape và backslash). "
            "In ra đúng MỘT dòng JSON dạng {\"module\": count} với sort_keys=True. Sau đó chạy `python parse_log.py` và báo kết quả.")


def check_t2(d, out):
    if not os.path.exists(f"{d}/parse_log.py"):
        return False, "không có parse_log.py"
    code, o = py(d, "parse_log.py")
    try:
        got = json.loads(o.strip().splitlines()[-1])
    except Exception:
        return False, "output không phải JSON: " + o[-200:]
    exp = json.loads(open(f"{d}/.expected", encoding="utf-8").read())
    return got == exp, f"got={got} exp={exp}"


INVENTORY = '''class Inventory:
    def __init__(self):
        self.items = {}

    def add(self, sku, qty):
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.items[sku] = qty  # BUG: ghi đè thay vì cộng dồn

    def remove(self, sku, qty):
        have = self.items.get(sku, 0)
        if qty > have:
            raise ValueError("not enough stock")
        self.items[sku] = have - qty
        if self.items[sku] < 0:  # BUG: phải xoá khi == 0
            del self.items[sku]

    def total(self):
        return sum(self.items.values())

    def low_stock(self, threshold):
        return sorted(s for s, q in self.items.items() if q < threshold)  # BUG: phải là <=
'''
INVENTORY = INVENTORY.replace("  # BUG: ghi đè thay vì cộng dồn", "").replace("  # BUG: phải xoá khi == 0", "").replace("  # BUG: phải là <=", "")
TEST_INV = '''import unittest
from inventory import Inventory

class T(unittest.TestCase):
    def test_add_accumulates(self):
        inv = Inventory(); inv.add("a", 2); inv.add("a", 3)
        self.assertEqual(inv.items["a"], 5)
    def test_remove_to_zero_deletes(self):
        inv = Inventory(); inv.add("a", 2); inv.remove("a", 2)
        self.assertNotIn("a", inv.items)
    def test_remove_too_much(self):
        inv = Inventory(); inv.add("a", 1)
        with self.assertRaises(ValueError): inv.remove("a", 2)
    def test_low_stock_inclusive(self):
        inv = Inventory(); inv.add("a", 5); inv.add("b", 6); inv.add("c", 1)
        self.assertEqual(inv.low_stock(5), ["a", "c"])
    def test_total(self):
        inv = Inventory(); inv.add("a", 2); inv.add("b", 3)
        self.assertEqual(inv.total(), 5)

if __name__ == "__main__":
    unittest.main()
'''


def setup_t3(d):
    w(f"{d}/inventory.py", INVENTORY)
    w(f"{d}/test_inventory.py", TEST_INV)
    return ("Chạy `python -m unittest -v`, tìm và sửa các bug trong inventory.py cho đến khi tất cả test pass. "
            "KHÔNG được sửa test_inventory.py. Cuối cùng chạy lại test để xác nhận.")


def check_t3(d, out):
    if hashlib.md5(open(f"{d}/test_inventory.py", "rb").read()).hexdigest() != hashlib.md5(TEST_INV.encode()).hexdigest():
        return False, "đã sửa file test"
    code, o = py(d, "-m", "unittest")
    return code == 0, o[-300:]


SHOP_FILES = {
    "shop/__init__.py": "",
    "shop/cart.py": '''from dataclasses import dataclass

@dataclass
class Item:
    name: str
    price: int
    qty: int
''',
    "shop/pricing.py": '''from .cart import Item

SHIPPING_FEE = 30000
FREE_SHIP_THRESHOLD = 500000


def subtotal(items: list[Item]) -> int:
    return sum(i.price * i.qty for i in items)


def calc_total(items: list[Item]) -> int:
    sub = subtotal(items)
    ship = 0 if sub >= FREE_SHIP_THRESHOLD else SHIPPING_FEE
    return sub + ship
''',
    "shop/cli.py": '''import argparse, json
from .cart import Item
from .pricing import calc_total


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("items_file")
    a = p.parse_args(argv)
    items = [Item(**x) for x in json.load(open(a.items_file, encoding="utf-8"))]
    print(f"TOTAL: {calc_total(items)}")


if __name__ == "__main__":
    main()
''',
    "tests/test_pricing.py": '''import unittest
from shop.cart import Item
from shop.pricing import calc_total

class T(unittest.TestCase):
    def test_ship(self):
        self.assertEqual(calc_total([Item("a", 100000, 2)]), 230000)
    def test_free_ship(self):
        self.assertEqual(calc_total([Item("a", 250000, 2)]), 500000)
''',
    "tests/__init__.py": "",
    "items.json": '[{"name": "ao", "price": 150000, "qty": 2}, {"name": "mu", "price": 50000, "qty": 1}]',
}
HIDDEN_TEST = '''import unittest, subprocess, sys
from shop.cart import Item
from shop.pricing import calc_total

I = [Item("a", 100000, 2)]          # subtotal 200000
BIG = [Item("a", 300000, 2)]        # subtotal 600000

class Hidden(unittest.TestCase):
    def test_no_code(self):
        self.assertEqual(calc_total(I), 230000)
    def test_save10(self):
        self.assertEqual(calc_total(I, code="SAVE10"), 210000)
    def test_save10_big(self):
        self.assertEqual(calc_total(BIG, code="SAVE10"), 540000)
    def test_freeship(self):
        self.assertEqual(calc_total(I, code="FREESHIP"), 200000)
    def test_invalid(self):
        with self.assertRaises(ValueError):
            calc_total(I, code="BOGUS")
    def test_cli(self):
        o = subprocess.run([sys.executable, "-m", "shop.cli", "items.json", "--code", "SAVE10"],
                           capture_output=True, text=True).stdout
        self.assertIn("TOTAL: 345000", o)   # 350000*0.9=315000 + ship 30000

if __name__ == "__main__":
    unittest.main()
'''


def setup_t4(d):
    for p, t in SHOP_FILES.items():
        w(f"{d}/{p}", t)
    return ("Đây là project Python nhỏ (package shop/). Hãy đọc code rồi thêm tính năng mã giảm giá:\n"
            "1. Đổi signature thành calc_total(items, code=None) trong shop/pricing.py.\n"
            "2. SAVE10: giảm 10% subtotal (kết quả là int). FREESHIP: miễn phí ship. code=None: như cũ. "
            "Mã khác: raise ValueError.\n"
            "3. Phí ship tính dựa trên subtotal TRƯỚC khi giảm giá.\n"
            "4. Thêm option --code vào shop/cli.py, output vẫn giữ định dạng `TOTAL: <số>`.\n"
            "5. Thêm test cho tính năng mới vào tests/test_pricing.py và chạy `python -m unittest discover -s tests -t .` cho đến khi pass.")


def check_t4(d, out):
    w(f"{d}/hidden_test.py", HIDDEN_TEST)
    code, o = py(d, "-m", "unittest", "hidden_test")
    return code == 0, o[-400:]


def setup_t5(d):
    rnd = random.Random(7)
    words = ["ledger", "invoice", "payout", "refund", "account", "batch", "settle", "audit", "fx", "fee"]
    for n in range(1, 25):
        out = [f'"""Module part{n:02d}: tiện ích cho hệ thống thanh toán."""', "import math", ""]
        if n == 1:
            out += ["RETRY_BACKOFF_MS = 7350", ""]
        for k in range(60):
            a, b = rnd.sample(words, 2)
            fname = f"{a}_{b}_{n:02d}_{k:02d}"
            callee = "reconcile_ledger" if (n == 17 and k == 29) else f"{b}_helper"
            if n == 17 and k == 29:
                fname = "close_monthly_books"
            out += [f"def {fname}(rows, rate={rnd.randint(1, 99)}):",
                    f'    """Tính {a} theo {b} cho batch {n}-{k}."""',
                    "    total = 0",
                    "    for r in rows:",
                    f"        total += r.get('{a}', 0) * rate / {rnd.randint(2, 9)}",
                    f"    return {callee}(total, rows)", ""]
        if n == 9:
            out += ["def reconcile_ledger(total, rows):", "    return round(total, 2)", ""]
        w(f"{d}/docs/part{n:02d}.py", "\n".join(out) + "\n")
    return ("Trong thư mục docs/ có 24 file part01.py đến part24.py. Hãy dùng tool read đọc TOÀN BỘ từng file theo thứ tự "
            "từ part01 đến part24 (không dùng grep/search, không dùng bash). Sau khi đọc xong cả 24 file, trả lời 3 câu:\n"
            "1. Hằng số RETRY_BACKOFF_MS được định nghĩa ở file nào và giá trị bao nhiêu?\n"
            "2. Hàm nào trong part17.py gọi reconcile_ledger?\n"
            "3. reconcile_ledger được định nghĩa ở file nào?")


def check_t5(d, out):
    ok = all(s in out for s in ["7350", "close_monthly_books"]) and "part01" in out and "part09" in out
    return ok, ""



# ---------------------------------------------------------------- web tasks (built-in webfetch tool)

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bench"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def setup_w1(d):
    return ("Dùng tool web fetch đọc https://pypi.org/pypi/requests/json và cho biết: phiên bản mới nhất của "
            "thư viện requests, và giá trị requires_python của nó. Không đoán, phải lấy từ trang.")


def check_w1(d, out):
    info = fetch_json("https://pypi.org/pypi/requests/json")["info"]
    ok = info["version"] in out and info["requires_python"].replace(" ", "") in out.replace(" ", "")
    return ok, f"exp version={info['version']} requires_python={info['requires_python']}"


def setup_w2(d):
    return ("Dùng tool web fetch gọi GitHub API https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5 "
            "và tìm release mới nhất có tên dạng bNNNNN (bỏ qua các tag khác). Cho biết tag đó, và nó có bao nhiêu asset "
            "là bản Windows x64 CUDA (tên chứa 'win-cuda' và 'x64', tính cả gói cudart). Ghi kết quả vào file release.json "
            "dạng {\"tag\": ..., \"win_cuda_x64_assets\": ...}.")


def check_w2(d, out):
    rels = fetch_json("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5")
    rel = next(r for r in rels if re.fullmatch(r"b\d+", r["tag_name"]))
    n = sum(1 for a in rel["assets"] if "win-cuda" in a["name"] and "x64" in a["name"])
    try:
        got = json.load(open(f"{d}/release.json", encoding="utf-8"))
    except Exception as e:
        return False, f"release.json: {e}"
    # A new build may land between the model's fetch and ours; accept the neighbouring release too.
    tags = [r["tag_name"] for r in rels if re.fullmatch(r"b\d+", r["tag_name"])][:2]
    return got.get("tag") in tags and got.get("win_cuda_x64_assets") == n, f"got={got} exp tag in {tags}, n={n}"


# ---------------------------------------------------------------- vision tasks (images generated with PIL)

def font(size, bold=False):
    from PIL import ImageFont
    return ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf", size)


V1_ROWS = [("Áo thun cổ tròn", 189000, 2), ("Quần jean slim", 459000, 1), ("Mũ lưỡi trai", 99000, 3),
           ("Giày thể thao", 1250000, 1), ("Tất cổ ngắn", 25000, 6), ("Balo chống nước", 535000, 1)]


def setup_v1(d):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (760, 420), "white")
    g = ImageDraw.Draw(img)
    g.text((24, 18), "ĐƠN HÀNG #A-2093", font=font(26, True), fill="#111")
    cols = [24, 380, 560, 680]
    y = 70
    for i, (a, b, c) in enumerate([("Sản phẩm", "Đơn giá (VND)", "SL")] + [(n, f"{p:,}".replace(",", "."), str(q)) for n, p, q in V1_ROWS]):
        if i == 0:
            g.rectangle([16, y - 6, 744, y + 34], fill="#e5e7eb")
        g.text((cols[0], y), a, font=font(22, i == 0), fill="#111")
        g.text((cols[1], y), b, font=font(22, i == 0), fill="#111")
        g.text((cols[3], y), c, font=font(22, i == 0), fill="#111")
        y += 48
        g.line([16, y - 8, 744, y - 8], fill="#d1d5db")
    img.save(f"{d}/order.png")
    return ("Ảnh order.png là một đơn hàng. Đọc ảnh và tạo file order.json là danh sách "
            "[{\"name\": str, \"price\": int, \"qty\": int}] đúng thứ tự trong ảnh (giữ nguyên tiếng Việt có dấu, "
            "price là số nguyên VND). Sau đó cho biết tổng tiền đơn hàng.", ["order.png"])


def check_v1(d, out):
    import unicodedata
    try:
        got = json.load(open(f"{d}/order.json", encoding="utf-8"))
        got = [(unicodedata.normalize("NFC", x["name"]).strip(), int(x["price"]), int(x["qty"])) for x in got]
    except Exception as e:
        return False, f"order.json: {e}"
    exp = [(unicodedata.normalize("NFC", n), p, q) for n, p, q in V1_ROWS]
    total = sum(p * q for _, p, q in V1_ROWS)
    total_ok = any(s in out.replace(".", "").replace(",", "") for s in [str(total)])
    bad = [f"{g} != {e}" for g, e in zip(got, exp) if g != e]
    return got == exp and total_ok, f"total_in_answer={total_ok} total={total} diffs={bad[:3]} len={len(got)}"


V2_DATA = [("T1", 42), ("T2", 58), ("T3", 35), ("T4", 77), ("T5", 64), ("T6", 51)]


def setup_v2(d):
    from PIL import Image, ImageDraw
    W, H, x0, y0, top = 720, 460, 80, 400, 60
    img = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(img)
    g.text((x0, 16), "Doanh thu 6 tháng đầu năm (triệu VND)", font=font(22, True), fill="#111")
    scale = (y0 - top) / 100
    for v in range(0, 101, 20):
        y = y0 - v * scale
        g.line([x0, y, W - 30, y], fill="#e5e7eb")
        g.text((x0 - 40, y - 10), str(v), font=font(16), fill="#555")
    bw = 60
    for i, (m, v) in enumerate(V2_DATA):
        x = x0 + 30 + i * 100
        g.rectangle([x, y0 - v * scale, x + bw, y0], fill="#2563eb")
        g.text((x + 18, y0 + 8), m, font=font(18), fill="#111")
    img.save(f"{d}/chart.png")
    return ("Ảnh chart.png là biểu đồ cột doanh thu (không ghi số trên cột, phải đọc theo trục tung). "
            "Trả lời: tháng nào cao nhất, tháng nào thấp nhất, và ước lượng doanh thu tháng T4 và T6 (số nguyên). "
            "Trả lời theo đúng định dạng 3 dòng:\nMAX: <tháng>\nMIN: <tháng>\nT4=<số>, T6=<số>", ["chart.png"])


def check_v2(d, out):
    mx = re.search(r"MAX:\s*(T\d)", out)
    mn = re.search(r"MIN:\s*(T\d)", out)
    t4 = re.search(r"T4\s*=\s*(\d+)", out)
    t6 = re.search(r"T6\s*=\s*(\d+)", out)
    if not all([mx, mn, t4, t6]):
        return False, "sai định dạng"
    ok = (mx.group(1) == "T4" and mn.group(1) == "T3" and abs(int(t4.group(1)) - 77) <= 5 and abs(int(t6.group(1)) - 51) <= 5)
    return ok, f"MAX={mx.group(1)} MIN={mn.group(1)} T4={t4.group(1)}(77) T6={t6.group(1)}(51)"


def setup_v3(d):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (520, 560), "#f3f4f6")
    g = ImageDraw.Draw(img)
    g.rounded_rectangle([40, 40, 480, 520], radius=16, fill="white", outline="#e5e7eb")
    g.text((80, 80), "Đăng nhập JINER", font=font(30, True), fill="#111827")
    g.text((80, 140), "Email", font=font(18, True), fill="#374151")
    g.rounded_rectangle([80, 168, 440, 212], radius=8, outline="#9ca3af")
    g.text((94, 180), "ban@vidu.com", font=font(17), fill="#9ca3af")
    g.text((80, 236), "Mật khẩu", font=font(18, True), fill="#374151")
    g.rounded_rectangle([80, 264, 440, 308], radius=8, outline="#9ca3af")
    g.text((94, 276), "••••••••", font=font(17), fill="#9ca3af")
    g.text((104, 325), "Ghi nhớ tôi", font=font(16), fill="#374151")
    g.rectangle([80, 326, 96, 342], outline="#6b7280")
    g.text((300, 326), "Quên mật khẩu?", font=font(16), fill="#e11d48")
    g.rounded_rectangle([80, 372, 440, 420], radius=8, fill="#e11d48")
    g.text((214, 384), "Tiếp tục", font=font(20, True), fill="white")
    g.text((120, 452), "Chưa có tài khoản? Đăng ký", font=font(16), fill="#374151")
    img.save(f"{d}/mockup.png")
    return ("Ảnh mockup.png là thiết kế một form đăng nhập. Viết file login.html (HTML + CSS trong 1 file, không dùng "
            "thư viện ngoài) tái hiện đúng giao diện: đủ mọi chữ, ô nhập, checkbox, link, và màu của nút chính.", ["mockup.png"])


def check_v3(d, out):
    try:
        h = open(f"{d}/login.html", encoding="utf-8").read()
    except Exception as e:
        return False, f"login.html: {e}"
    import unicodedata
    h = unicodedata.normalize("NFC", h)
    need = ["Đăng nhập JINER", "Email", "Mật khẩu", "Ghi nhớ tôi", "Quên mật khẩu?", "Tiếp tục", "Đăng ký"]
    miss = [s for s in need if s not in h]
    if 'type="password"' not in h.replace("'", '"'):
        miss.append("input password")
    if 'type="checkbox"' not in h.replace("'", '"'):
        miss.append("checkbox")
    # button colour: any red-ish hex close to #e11d48
    reds = re.findall(r"#([0-9a-fA-F]{6})\b", h)
    red_ok = any(int(c[0:2], 16) > 180 and int(c[2:4], 16) < 90 and int(c[4:6], 16) < 120 for c in reds)
    if not red_ok:
        miss.append("màu đỏ nút")
    return not miss, f"missing={miss}"


# ---------------------------------------------------------------- browser tasks (chrome-devtools-mcp, headless)

CHROME = os.environ.get("CHROME_BIN") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def dump_dom(path):
    """Render a local page in headless Chrome and return the DOM after scripts ran."""
    url = "file:///" + os.path.abspath(path).replace("\\", "/")
    r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--allow-file-access-from-files",
                        "--virtual-time-budget=3000", "--dump-dom", url], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return r.stdout


B1_DATA = "window.ITEMS = [{name: 'a', price: 1, qty: 2}, {name: 'b', price: 2, qty: 2}];\n"


def setup_b1(d):
    w(f"{d}/index.html", '<!doctype html><html><head><meta charset="utf-8"><title>Cart</title></head><body>\n'
      '<h1>Giỏ hàng</h1><p id="total">Đang tính...</p>\n'
      '<script src="data.js"></script><script src="app.js"></script></body></html>\n')
    w(f"{d}/data.js", B1_DATA)
    w(f"{d}/app.js", "function render() {\n"
      "  const total = window.ITEMS.reduce((s, i) => s + i.price * i.qty);\n"
      "  document.getElementById('totl').textContent = 'Total: ' + total;\n"
      "}\nwindow.addEventListener('load', render);\n")
    url = "file:///" + os.path.abspath(f"{d}/index.html").replace("\\", "/")
    return ("Trang index.html trong thư mục này đang hiển thị sai. Dùng chrome-devtools mở " + url +
            ", đọc console errors và DOM để tìm nguyên nhân, sửa app.js (KHÔNG sửa data.js, không hard-code kết quả) "
            "cho đến khi trang hiển thị đúng 'Total: 6' và không còn console error. Mở lại trang để xác nhận.")


def check_b1(d, out):
    if open(f"{d}/data.js", encoding="utf-8").read() != B1_DATA:
        return False, "đã sửa data.js"
    if "Total: 6" not in dump_dom(f"{d}/index.html"):
        return False, "trang chưa hiện Total: 6"
    # anti-hardcode: change the data and expect the new total (3*3 + 4*1 = 13)
    w(f"{d}/data.js", "window.ITEMS = [{name: 'x', price: 3, qty: 3}, {name: 'y', price: 4, qty: 1}];\n")
    dom = dump_dom(f"{d}/index.html")
    return "Total: 13" in dom, "với data khác: " + (re.search(r'id="total"[^>]*>([^<]*)', dom) or [None, "?"])[1]


def setup_b2(d):
    os.makedirs(f"{d}/lib", exist_ok=True)
    w(f"{d}/lib/mini-chart.js", "window.MiniChart = { draw(el, xs) { el.textContent = 'Chart OK: ' + xs.join(','); } };\n")
    w(f"{d}/index.html", '<!doctype html><html><head><meta charset="utf-8"><title>Dashboard</title>\n'
      '<script src="vendor/mini-chart.min.js"></script></head><body>\n'
      '<div id="chart">loading</div>\n'
      "<script>try { MiniChart.draw(document.getElementById('chart'), [3, 1, 4]); }\n"
      "catch (e) { document.getElementById('chart').textContent = 'Không vẽ được biểu đồ'; }</script>\n"
      '</body></html>\n')
    url = "file:///" + os.path.abspath(f"{d}/index.html").replace("\\", "/")
    return ("Dashboard " + url + " hiển thị 'Không vẽ được biểu đồ' nhưng lỗi bị try/catch nuốt mất. Dùng chrome-devtools "
            "(network requests, console, có thể inject initScript để bắt lỗi) để tìm nguyên nhân gốc, sửa index.html, "
            "rồi mở lại trang xác nhận biểu đồ hiển thị 'Chart OK: 3,1,4'. Không sửa thư mục lib/.")


def check_b2(d, out):
    dom = dump_dom(f"{d}/index.html")
    return "Chart OK: 3,1,4" in dom, re.sub(r"\s+", " ", dom)[-200:]


TASKS = {
    "base": [("T1_read", setup_t1, check_t1, 600), ("T2_write_regex", setup_t2, check_t2, 900),
             ("T3_bugfix", setup_t3, check_t3, 1200), ("T4_feature", setup_t4, check_t4, 1800),
             ("T5_long_ctx", setup_t5, check_t5, 2700)],
    "web": [("W1_fetch_pypi", setup_w1, check_w1, 600), ("W2_fetch_github", setup_w2, check_w2, 900)],
    "vision": [("V1_table_ocr", setup_v1, check_v1, 900), ("V2_chart", setup_v2, check_v2, 600),
               ("V3_mockup_html", setup_v3, check_v3, 1200)],
    "browser": [("B1_console_fix", setup_b1, check_b1, 1200), ("B2_hidden_404", setup_b2, check_b2, 1200)],
}

# server = run-qwen.ps1 -Model value; None = cloud agent (no local server)
MODELS = {
    "q4": {"server": "q4", "alias": "qwen36-uncensored-q4"},
    "q4v": {"server": "q4", "alias": "qwen36-uncensored-q4", "vision": True},
    "q5": {"server": "q5", "alias": "qwen36-uncensored-q5"},
    "orig": {"server": "orig", "alias": "qwen36-original-q4"},
    "claude": {"server": None},
}


def run_opencode(cfg, d, prompt, files, timeout, browser):
    cmd = [OPENCODE, "run", "-m", f"llama.cpp/{cfg['alias']}", prompt]  # prompt before -f (array flag eats the rest)
    for f in files:
        cmd += ["-f", f]
    # bench config overrides the user's chrome-devtools entry with an isolated, headless browser
    env = dict(os.environ, OPENCODE_CONFIG=os.path.join(BENCH, "opencode.bench.json"))
    r = subprocess.run(cmd, cwd=d, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace", env=env)
    return ANSI.sub("", (r.stdout or "") + (r.stderr or "")), {}


def run_claude(cfg, d, prompt, files, timeout, browser):
    if files:
        prompt = "Các file ảnh nằm trong thư mục hiện tại: " + ", ".join(files) + ". Dùng tool Read để xem ảnh.\n\n" + prompt
    tools = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "WebFetch"]
    mcp = []
    if browser:  # same isolated headless chrome-devtools-mcp the local models get
        mcp, tools = ["--mcp-config", os.path.join(BENCH, "mcp-chrome.claude.json")], tools + ["mcp__chrome-devtools"]
    cmd = [shutil.which("claude") or "claude", "-p", prompt, "--output-format", "json",
           "--permission-mode", "acceptEdits", "--strict-mcp-config", *mcp, "--allowedTools", *tools]
    r = subprocess.run(cmd, cwd=d, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
    try:
        j = json.loads(r.stdout)
    except Exception:
        return (r.stdout or "") + (r.stderr or ""), {}
    meta = {"cost_usd": j.get("total_cost_usd"), "turns": j.get("num_turns"),
            "api_s": round((j.get("duration_api_ms") or 0) / 1000), "models": list((j.get("modelUsage") or {}).keys())}
    return j.get("result") or "", meta


def run_task(model, outdir, name, setup, check, timeout):
    cfg = MODELS[model]
    d = os.path.join(outdir, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    s = setup(d)
    prompt, files = (s, []) if isinstance(s, str) else s
    t0 = time.time()
    try:
        runner = run_claude if cfg["server"] is None else run_opencode
        out, meta = runner(cfg, d, prompt, files, timeout, browser=name.startswith("B"))
        timed_out = False
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        out, meta, timed_out = ANSI.sub("", out), {}, True
        if cfg["server"] is not None:
            subprocess.run(["taskkill", "/F", "/IM", "opencode.exe", "/T"], capture_output=True)
    el = time.time() - t0
    w(os.path.join(outdir, f"{name}.out.txt"), out)
    try:
        ok, detail = (False, "timeout") if timed_out else check(d, out)
    except Exception as e:
        ok, detail = False, f"checker error: {e}"
    log(f"  [{model}] {name}: {'PASS' if ok else 'FAIL'} in {el:.0f}s  {meta or ''} {detail[:150] if not ok else ''}")
    return {"task": name, "pass": ok, "seconds": round(el), "detail": detail[:500], **meta}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+", choices=list(MODELS))
    ap.add_argument("--tasks", default="base", help="comma list of: " + ",".join(TASKS) + " (or 'all')")
    ap.add_argument("--no-speed", action="store_true")
    ap.add_argument("--reuse-server", action="store_true",
                    help="use the llama-server already on :8080 (must be the right model) and leave it running")
    a = ap.parse_args()
    groups = list(TASKS) if a.tasks == "all" else a.tasks.split(",")
    os.makedirs(RESULTS, exist_ok=True)
    for model in a.models:
        cfg = MODELS[model]
        outdir = os.path.join(RESULTS, model)
        os.makedirs(outdir, exist_ok=True)
        rpath = os.path.join(outdir, "result.json")
        res = json.load(open(rpath, encoding="utf-8")) if os.path.exists(rpath) else {}
        log(f"=== {model} tasks={groups} ===")
        reuse = False
        if cfg["server"] and a.reuse_server:
            try:
                running = http("/v1/models", timeout=3)["data"][0]["id"]
            except Exception:
                running = None
            if running != cfg["alias"]:
                sys.exit(f"--reuse-server: running model is {running}, expected {cfg['alias']}")
            reuse = True
            log(f"  [{model}] reusing running server")
        if cfg["server"] and not reuse:
            load_s = start_server(cfg["server"], outdir, cfg.get("vision", False))
            res["load_s"], res["mem_after_load"] = round(load_s), mem_snapshot()
            log(f"  [{model}] loaded in {load_s:.0f}s  {res['mem_after_load']}")
            if not a.no_speed:
                res["speed"] = speed_test(outdir)
        done = {t["task"]: t for t in res.get("tasks", [])}
        for gname in groups:
            for t in TASKS[gname]:
                done[t[0]] = run_task(model, outdir, *t)
                res["tasks"] = list(done.values())
                json.dump(res, open(rpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        if cfg["server"] and not reuse:
            res["mem_end"] = mem_snapshot()
            json.dump(res, open(rpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            kill_server()
    log("=== DONE ===")


if __name__ == "__main__":
    main()
