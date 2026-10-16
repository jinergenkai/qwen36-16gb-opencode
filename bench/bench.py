"""Benchmark Qwen3.6 local: tốc độ (prefill/decode theo độ sâu context) + 5 task OpenCode tăng dần độ khó.

Usage: python bench.py q4 orig q5      (mỗi model: start server -> speed -> tasks -> stop)
Kết quả: bench\\results\\<model>\\...  và  bench\\results\\summary.json
"""
import json, os, random, re, shutil, subprocess, sys, time, hashlib, urllib.request

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
RESULTS = os.path.join(BENCH, "results")
ALIAS = {"q4": "qwen36-uncensored-q4", "q5": "qwen36-uncensored-q5", "orig": "qwen36-original-q4"}
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


def start_server(model, outdir):
    kill_server()
    logf = open(os.path.join(outdir, "server.log"), "w", encoding="utf-8", errors="replace")
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                      os.path.join(ROOT, "run-qwen.ps1"), "-Model", model], stdout=logf, stderr=subprocess.STDOUT)
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


TASKS = [("T1_read", setup_t1, check_t1, 600), ("T2_write_regex", setup_t2, check_t2, 900),
         ("T3_bugfix", setup_t3, check_t3, 1200), ("T4_feature", setup_t4, check_t4, 1800),
         ("T5_long_ctx", setup_t5, check_t5, 2700)]


def run_task(model, outdir, name, setup, check, timeout):
    d = os.path.join(outdir, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    prompt = setup(d)
    t0 = time.time()
    try:
        r = subprocess.run([OPENCODE, "run", "-m", f"llama.cpp/{ALIAS[model]}", prompt], cwd=d, capture_output=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        out = ANSI.sub("", (r.stdout or "") + (r.stderr or ""))
        timed_out = False
    except subprocess.TimeoutExpired as e:
        out = ANSI.sub("", (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
        timed_out = True
        subprocess.run(["taskkill", "/F", "/IM", "opencode.exe", "/T"], capture_output=True)
    el = time.time() - t0
    w(os.path.join(outdir, f"{name}.out.txt"), out)
    ok, detail = (False, "timeout") if timed_out else check(d, out)
    tools = len(re.findall(r"^\s*(?:←|\$|→|✱|%)\s", out, re.M))
    log(f"  {name}: {'PASS' if ok else 'FAIL'} in {el:.0f}s  {detail[:150] if not ok else ''}")
    return {"task": name, "pass": ok, "seconds": round(el), "detail": detail[:500], "tool_lines": tools}


def main():
    os.makedirs(RESULTS, exist_ok=True)
    summary_path = os.path.join(RESULTS, "summary.json")
    summary = json.load(open(summary_path, encoding="utf-8")) if os.path.exists(summary_path) else {}
    for model in sys.argv[1:]:
        outdir = os.path.join(RESULTS, model)
        os.makedirs(outdir, exist_ok=True)
        log(f"=== {model} ({ALIAS[model]}) ===")
        load_s = start_server(model, outdir)
        mem = mem_snapshot()
        log(f"  loaded in {load_s:.0f}s  {mem}")
        res = {"load_s": round(load_s), "mem_after_load": mem, "speed": speed_test(outdir), "tasks": []}
        for t in TASKS:
            res["tasks"].append(run_task(model, outdir, *t))
            summary[model] = res
            json.dump(summary, open(summary_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        res["mem_end"] = mem_snapshot()
        summary[model] = res
        json.dump(summary, open(summary_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        kill_server()
    log("=== DONE ===")


if __name__ == "__main__":
    main()
