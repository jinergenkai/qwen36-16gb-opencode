# Qwen3.6-35B-A3B on a 16GB GPU, as a local OpenCode agent

Scripts and a benchmark for running **Qwen3.6-35B-A3B** (MoE, ~3B active) with **llama.cpp + OpenCode** on a mid-range Windows desktop, using the full **262K context**, plus measured results for three quants.

Tested machine: RTX 5060 Ti 16GB · i5-12400F (6C/12T) · 32GB RAM · Windows 11 · llama.cpp b11669 (CUDA 13.4) · OpenCode 1.19.2.

## Results (2026-10-16)

### Speed

| Context | Q4_K_P uncensored | UD-Q4_K_XL original | Q5_K_P uncensored |
|---|---|---|---|
| 4.9K tokens: prefill / decode | 1547 / 45.9 t/s | 1686 / 45.2 | 767 / 40.5 |
| 41.6K | 1729 / 41.6 | 1898 / 41.2 | 996 / 36.3 |
| 174K | 1265 / 29.0 | 1347 / 28.2 | 831 / 26.2 |
| llama-server RAM, after load / end of run | 14.6 / 19.3 GB | 13.6 / 18.9 GB | 19.0 / 20.7 GB |

VRAM stays at ~14.6 / 16.3 GB for all three (`--fit` puts the remaining experts in system RAM).

### Agent tasks (OpenCode `run`, auto-graded, one run each)

| Task | What it checks | Q4 unc. | Q4 orig. | Q5 unc. |
|---|---|---|---|---|
| T1 read | read 3 files, pick the live secret over a revoked decoy | ✅ 68s | ✅ 78s | ✅ 76s |
| T2 write + regex | write a log parser whose source is full of quotes/backslashes (classic tool-call JSON breaker), run it, exact output match | ✅ 78s | ✅ 87s | ✅ 113s |
| T3 bugfix | run unit tests, fix 3 bugs, test file must stay untouched | ✅ 63s | ✅ 74s | ✅ 103s |
| T4 feature | multi-file change + CLI flag, graded by **hidden tests** | ✅ 126s | ❌ 145s¹ | ✅ 201s |
| T5 long context | read 24 files (~145K tokens) one by one, answer 3 recall questions, no grep allowed | ✅² 245s | ✅ 204s | ✅ 1072s³ |

1. Discount logic right, but `cli.py` referenced an undefined `args` variable and the model never ran the CLI.
2. Read all 24 files, then used Grep to double-check despite being told not to.
3. One turn got stuck thinking for 32,000 tokens (the output cap) before recovering.

Zero tool-call JSON parse errors across all runs. Raw outputs: [`bench/results/`](bench/results/).

**Takeaway:** Q4_K_P is the sweet spot on 32GB RAM. Q5 halves prefill speed and leaves ~5GB RAM free without a measurable quality gain in these tasks. Treat single-run differences as anecdotal.

## Setup

1. **llama.cpp**: download `llama-bXXXX-bin-win-cuda-13.x-x64.zip` and `cudart-llama-bin-win-cuda-13.x-x64.zip` from [releases](https://github.com/ggml-org/llama.cpp/releases), extract both into `llama.cpp/`. (RTX 50-series needs a CUDA 12.8+ build.)
2. **Models**:
   ```powershell
   pip install -U huggingface_hub
   .\download-all.ps1          # Q4 uncensored + fixed chat template (~22GB)
   .\download-all.ps1 -All     # + original Q4, vision mmproj, Q5 (~70GB total)
   ```
3. **Run**: double-click `run-q4.bat` (or `run-q5.bat`, `run-orig.bat`). Ready when the log says `listening on http://127.0.0.1:8080`.
4. **OpenCode**: `npm i -g opencode-ai`, copy [`opencode.example.json`](opencode.example.json) to `%USERPROFILE%\.config\opencode\opencode.json`, then run `opencode` in your project and pick the model with `/models`.
5. **Benchmark** (optional): `python bench/bench.py q4 orig q5`. It starts/stops the server itself.

## Why these flags

| Flag | Reason |
|---|---|
| `--chat-template-file` (fixed template) | The official Qwen3.6 template uses `\|items` and rejects the `developer` role, which breaks tool calls in llama.cpp / OpenCode. Uses [sudiptosarkar/Qwen-Fixed-Chat-Templates](https://huggingface.co/sudiptosarkar/Qwen-Fixed-Chat-Templates). |
| `-c 262144 --fit on --fit-ctx 262144` | Full native context. Without `--fit-ctx`, `--fit` silently shrinks context to fit. Only 10 of 40 layers use full attention, so KV cache for 262K is ~2.7GB at q8. |
| `--fit-target 1024` | Leave ~1GB VRAM for Windows so the driver never spills into shared memory (silent 2x slowdown). |
| `--load-mode none` | With mmap, the whole 22GB file stayed in the process working set (RAM free: 0.6GB). Without it: 14GB, and prefill went from ~700 to ~1600 t/s. |
| `--cache-ram 8192` (4096 for Q5) | The prompt cache lives in RAM and grew the server by ~5GB over the benchmark. |
| `-ctk q8_0 -ctv q8_0` | Near-lossless; q4 K-cache hurts tool-call reliability. |
| `-np 1` | One user; extra slots cost recurrent-state memory. |
| `preserve_thinking` | Qwen3.6 is trained to keep previous reasoning across tool turns. |
| temp 0.6, top-p 0.95, top-k 20 | Qwen's recommended thinking-mode coding preset. |

Also recommended: NVIDIA Control Panel → CUDA Sysmem Fallback Policy → *Prefer No Sysmem Fallback*, so VRAM overflow errors out instead of silently slowing down.

## Layout

```
download-all.ps1        model + template downloader (resumable, keeps PC awake)
run-qwen.ps1            llama-server launcher (-Model q4|q5|orig, -Ctx, -Vision)
run-*.bat               double-click wrappers
opencode.example.json   OpenCode provider config
bench/bench.py          speed + agent-task benchmark
bench/results/          summary.json, bench.log, per-task model transcripts
```

## Models

- [HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive](https://huggingface.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive)
- [unsloth/Qwen3.6-35B-A3B-GGUF](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) (baseline)

Model weights are not included; check each model's license before use.
