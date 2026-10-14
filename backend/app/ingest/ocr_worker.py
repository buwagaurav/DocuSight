"""DeepSeek-OCR-2 worker process (Apple Silicon, MLX).

Runs as a child of the API server so MLX/Metal never shares a process with the
PyTorch models (ColPali, EmbeddingGemma), and so its ~4 GB can be released when idle.

Protocol (JSON lines):
    stdin  {"id": "...", "image": "/path/page.png", "max_tokens": 6144}
    stdout {"ready": true, "load_seconds": 4.1}                 (once, after loading)
           {"id": "...", "text": "...", "seconds": 8.0, "tokens": 563}
           {"id": "...", "error": "..."}

    python -m app.ingest.ocr_worker mlx-community/DeepSeek-OCR-2-4bit
"""
from __future__ import annotations

import json
import os
import sys
import time

PROMPT = "<|grounding|>Convert the document to markdown."


def main() -> None:
    model_id = sys.argv[1] if len(sys.argv) > 1 else "mlx-community/DeepSeek-OCR-2-4bit"
    # Libraries print to stdout (e.g. "Added grounding tokens"); keep stdout clean for the protocol.
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def send(obj: dict) -> None:
        proto.write(json.dumps(obj) + "\n")
        proto.flush()

    t = time.time()
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template

    # trust_remote_code=False: the repo's bundled PyTorch code targets transformers 4.x and
    # breaks on 5.x; MLX inference uses mlx-vlm's own DeepSeek-OCR-2 implementation instead.
    model, processor = load(model_id, trust_remote_code=False)
    prompt = apply_chat_template(processor, model.config, PROMPT, num_images=1)
    send({"ready": True, "load_seconds": round(time.time() - t, 1)})

    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        try:
            t = time.time()
            out = generate(model, processor, prompt, image=[req["image"]], max_tokens=int(req.get("max_tokens", 6144)),
                           temperature=0.0, verbose=False)
            send({"id": req.get("id"), "text": getattr(out, "text", str(out)),
                  "seconds": round(time.time() - t, 2), "tokens": getattr(out, "generation_tokens", None)})
        except Exception as e:  # report and keep serving
            send({"id": req.get("id"), "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
