# Self-hosted vLLM serving

The agent talks to any OpenAI-compatible Chat Completions endpoint through
`ModelClient` (`components/agent_cli/llm_client.py`). Switching from hosted
DeepSeek to a self-hosted vLLM server is an env change, not a code change:

```powershell
$env:CITRUS_LLM_PROVIDER="vllm"
$env:CITRUS_LLM_BASE_URL="https://<tunnel-or-host>/v1"
$env:CITRUS_LLM_MODEL="Qwen/Qwen2.5-1.5B-Instruct"
# vLLM without --api-key accepts any key; the client defaults to EMPTY
cd components
python -m agent_cli "What is the status of frontend pods in citrus?"
```

Reliability (semaphore concurrency cap, per-request timeout, retry with
backoff+jitter) and metrics (`citrus_llm_request_seconds` etc.) apply to both
providers — the serving layer is swappable, the harness is not.

## Why a small model + rented GPU is enough

The goal is not to host production inference. It is to prove the serving
abstraction end-to-end: same agent, same tools, same evidence stamp, different
inference backend, with latency/failure/token metrics on both sides.
Qwen2.5-1.5B-Instruct supports tool calling, fits a free Colab T4 (16 GB), and
is good enough to complete the ReAct loop on simple diagnostic queries.

## Option A: Google Colab (free T4)

Do **not** start the Cloudflare tunnel until `curl localhost:8000/v1/models`
returns JSON. A live `trycloudflare.com` URL with HTTP 502 means the tunnel
is up and vLLM is not.

### 0. Runtime

`Runtime → Change runtime type → T4 GPU`. Then `Runtime → Disconnect and
delete runtime` if the previous session was CPU, and reconnect. Confirm
**before** installing anything:

```python
import sys, torch
print("python", sys.version.split()[0])
print("cuda available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0))
!nvidia-smi -L
assert torch.cuda.is_available(), (
    "This session is CPU. Change runtime type to T4 GPU, then "
    "Disconnect and delete runtime, reconnect, and re-run this cell."
)
```

`google-adk` / `protobuf` pip warnings are Colab preinstall noise. Ignore them.

### 1. Install (then restart)

Colab (2026) ships CUDA **runtime 12.8**. A plain `pip install vllm` pulls the
current PyPI wheel built against CUDA **13**, so vLLM cannot infer a device
(`RuntimeError: Failed to infer device type` in `vllm/config/device.py`).

Pin a CUDA-12.8-compatible wheel, drop the unofficial `pynvml` package that
shadows NVML, and **restart the session** after this cell:

```python
!pip -q uninstall -y pynvml
!pip -q install -U nvidia-ml-py
!pip -q install "vllm==0.19.0"
print("Restart now: Runtime → Restart session. Do not re-run this cell.")
```

### 2. Serve (after restart)

Re-run cell 0 to confirm CUDA is still visible, then:

```python
import os, time, urllib.request

os.environ["VLLM_LOGGING_LEVEL"] = "INFO"
os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"  # Colab kernel already holds CUDA
os.environ["VLLM_USE_FLASHINFER_SAMPLER"] = "0"       # T4 (SM 7.5) is pre-Ampere
os.environ["VLLM_ATTENTION_BACKEND"] = "TRITON_ATTN"  # FA2/FlashInfer warmup hangs on T4

!pkill -f "vllm.entrypoints.openai.api_server" || true
!rm -f vllm.log
!nohup env VLLM_WORKER_MULTIPROC_METHOD=spawn VLLM_USE_FLASHINFER_SAMPLER=0 \
  VLLM_ATTENTION_BACKEND=TRITON_ATTN \
  python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-1.5B-Instruct \
    --dtype float16 --max-model-len 4096 --gpu-memory-utilization 0.85 \
    --enforce-eager \
    --enable-auto-tool-choice --tool-call-parser hermes \
    --host 127.0.0.1 --port 8000 > vllm.log 2>&1 &

ok = False
for i in range(72):  # ~6 min; weights are cached after the first attempt
    time.sleep(5)
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=2)
        ok = True
        break
    except Exception:
        print(f"waiting… {(i+1)*5}s")
print(open("vllm.log").read()[-2500:])
assert ok, "vLLM did not become ready. Scroll vllm.log above for the real error."
print("READY")
```

T4 notes baked into that command:

- `float16` not `bfloat16` (Turing has no native bf16).
- `--enforce-eager` skips CUDA graphs that often abort the Colab worker.
- `max-model-len 4096` (not 8192) leaves KV-cache headroom on 16 GB.
- `spawn` because forking from a live Colab CUDA context fails engine init.
- `VLLM_ATTENTION_BACKEND=TRITON_ATTN` because FA2 needs SM >= 8 and FlashInfer warmup stalls on T4.

### 3. Tunnel only after READY

```python
!wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared
!pkill -f cloudflared || true
!rm -f tunnel.log
!nohup ./cloudflared tunnel --url http://127.0.0.1:8000 > tunnel.log 2>&1 &
import time, re
time.sleep(8)
text = open("tunnel.log").read()
print(text)
m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
assert m, "no trycloudflare URL yet — re-run this cell"
print("PUBLIC", m.group(0))
```

### 4. Smoke test, then switch Citrus

From the notebook:

```python
!curl -s http://127.0.0.1:8000/v1/models
!curl -s http://127.0.0.1:8000/metrics | grep -E "vllm:(num_requests_running|time_to_first_token|e2e_request_latency|gpu_cache_usage)" | head -20
```

From the Windows machine (replace the URL, keep `/v1`):

```powershell
$env:CITRUS_LLM_PROVIDER="vllm"
$env:CITRUS_LLM_BASE_URL="https://<random>.trycloudflare.com/v1"
$env:CITRUS_LLM_MODEL="Qwen/Qwen2.5-1.5B-Instruct"
cd C:\app\Projects\Citrus-Orchestrator\components
python -m agent_cli "What is the status of frontend pods in citrus?"
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to infer device type` | CPU runtime, or CUDA-13 wheel on CUDA-12.8 Colab | Cell 0 must print a GPU. Then pin `vllm==0.19.0`, restart, serve. |
| `trycloudflare` 502 Bad Gateway | Tunnel up, nothing on :8000 | Do not tunnel until cell 2 prints READY. |
| Engine core init failed / CUDA re-init | Worker forked from Colab kernel | `VLLM_WORKER_MULTIPROC_METHOD=spawn` |
| OOM / KV cache full on T4 | 8192 context on 1.5B | `--max-model-len 4096 --gpu-memory-utilization 0.85 --enforce-eager` |
| Wait loop times out at "Warming up FlashInfer attention" | FA2 unsupported on T4; FlashInfer warmup hangs | Kill the server, set `VLLM_ATTENTION_BACKEND=TRITON_ATTN`, re-serve. Weights stay cached. |

## Option B: rented GPU (RunPod / Vast, ~$0.3/h)

Same serve flags, no tunnel if the pod exposes port 8000. Prefer this for the
load test — Colab free-tier sessions die mid-sweep.

```bash
pip install "vllm==0.19.0"
vllm serve Qwen/Qwen2.5-1.5B-Instruct \
  --dtype float16 --max-model-len 4096 --gpu-memory-utilization 0.85 \
  --enforce-eager \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --host 0.0.0.0 --port 8000
```

## Load test

`scripts/tests/load_test_llm.py` sweeps concurrency against any
OpenAI-compatible endpoint and reports per-level P50/P95 latency, throughput,
and token rate:

```powershell
python scripts\tests\load_test_llm.py `
  --base-url https://<random>.trycloudflare.com/v1 `
  --model Qwen/Qwen2.5-1.5B-Instruct `
  --concurrency 1 4 16 --requests-per-level 24 --max-tokens 128 `
  --output data\eval\load_test_vllm.json
```

What to look for:

- Throughput should rise from concurrency 1 → 4: continuous batching
  amortizes decode steps across requests.
- P95 latency degrades as concurrency approaches KV-cache limits; watch
  `vllm:num_requests_waiting` climb — that is the queue forming.
- A T4 with a 1.5B model saturates early; the point is to *measure* where,
  not to hit big numbers.

## Model serving & performance (results)

Recorded 2026-09-20 against Colab T4 + Cloudflare quick tunnel.
Model: `Qwen/Qwen2.5-1.5B-Instruct`, `--max-model-len 4096`, `--dtype float16`,
`--enforce-eager`, `VLLM_ATTENTION_BACKEND=TRITON_ATTN`.
Client: `scripts/tests/load_test_llm.py`, 12 requests per level, `max_tokens=128`.

`ModelClient` switch (env only) also succeeded against this endpoint. A later
cluster RCA used the same URL: 5 MCP tool calls, evidence HIGH, after tightening
`max_tokens` / tool truncation for the 4096 context window. The 1.5B model is
for serving proof, not RCA quality.

| Concurrency | Requests | P50 (s) | P95 (s) | Throughput (req/s) | Output tok/s |
|---|---|---|---|---|---|
| 1 | 12/12 | 3.109 | 3.469 | 0.323 | 34.2 |
| 4 | 12/12 | 3.266 | 3.844 | 1.096 | 115.7 |
| 16 | 12/12 | 14.141 | 14.610 | 0.814 | 75.6 |

Reading the table:

- 1 → 4: P50 stays ~3.1–3.3 s while throughput goes **0.323 → 1.096 req/s** (~3.4×) and tok/s **34 → 116**. Continuous batching shares decode steps across in-flight requests, so latency barely moves and goodput climbs.
- 4 → 16: P95 jumps **3.8 s → 14.6 s** and throughput *falls* to 0.814 req/s. The T4 is past the useful batch size; extra concurrency queues rather than computing. After the sweep, `vllm:num_requests_running` and `kv_cache_usage_perc` were back at 0 (idle).
- Serving-side totals after the session: 38 completed requests, mean TTFT **3.43 s** (`time_to_first_token_seconds_sum / count`), mean e2e **6.37 s**.

These are a one-shot T4 + tunnel measurement, not production SLOs.
