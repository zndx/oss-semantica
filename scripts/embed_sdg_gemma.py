#!/usr/bin/env python3
"""Encode SDG ontology and SKOS nodes with google/embeddinggemma-2.

One process per GPU, matching Aegir's CUDA_VISIBLE_DEVICES pattern. The HTTP
server never loads the model. It reads the NumPy archive this script writes.

Run the collector with the project interpreter (it imports Semantica). The
GPU workers use a separate environment so torch stays out of the server venv::

    uv venv .devenv/state/embed-venv --python 3.12
    uv pip install --python .devenv/state/embed-venv/bin/python \\
        torch --index-url https://download.pytorch.org/whl/cu128
    uv pip install --python .devenv/state/embed-venv/bin/python \\
        'sentence-transformers>=6.1.0' pillow
    uv pip install --python .devenv/state/embed-venv/bin/python \\
        torchvision --index-url https://download.pytorch.org/whl/cu128

Then, from the repo root, after the named GPUs have no compute processes::

    uv run --extra explorer python scripts/embed_sdg_gemma.py --gpus 0,1,2,3,4,5

The model is text-only (vision and audio configs are dropped). Precision is
bfloat16 when the GPU supports it, otherwise float32. float16 is not used:
EmbeddingGemma activations overflow. Every node gets the symmetric
SentenceSimilarity prompt, and vectors are L2-normalized 768-d.

ColBERT / multi-vector models are not used. Explorer semantic distance is
one cosine per node.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_DEFAULT_CORPORA = _REPO / "components" / "sdg-corpora"
_DEFAULT_OUT = _REPO / ".devenv" / "state" / "embeddings" / "sdg-embeddinggemma-2.npz"
_DEFAULT_PYTHON = _REPO / ".devenv" / "state" / "embed-venv" / "bin" / "python"
_DEFAULT_CUDA_LIBS = Path.home() / "local" / "src" / "zndx" / "aegir" / "build" / "cuda-driver-libs"
_MIN_BUSY_MIB = 512


def _parse_gpus(text: str) -> list[int]:
    gpus = [int(part) for part in text.split(",") if part.strip()]
    if not gpus:
        raise argparse.ArgumentTypeError("name at least one GPU")
    if len(set(gpus)) != len(gpus):
        raise argparse.ArgumentTypeError("GPU list has duplicates")
    return gpus


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpora", type=Path, default=_DEFAULT_CORPORA)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    parser.add_argument("--gpus", type=_parse_gpus, default=[0, 1, 2, 3, 4, 5])
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--python", type=Path, default=_DEFAULT_PYTHON)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--worker",
        nargs=2,
        metavar=("INPUT_JSONL", "OUTPUT_NPY"),
        help=argparse.SUPPRESS,
    )
    return parser


def collect_nodes(corpora: Path) -> list[tuple[str, str]]:
    """Union of the OWL projection and the SKOS nodes, keyed by IRI.

    The longer text wins when the same IRI appears in more than one file.
    That keeps a comment from the comprehensive OWL when the catalog only
    has the label, and the other way around.
    """
    from semantica.explorer.embedding_cache import embedding_text
    from semantica.explorer.routes.ontology import _convert_ontology_to_graph
    from semantica.explorer.utils.rdf_parser import parse_skos_file
    from semantica.ingest.ontology_ingestor import OntologyIngestor

    owl_files = [
        (corpora / "ontology" / "sdg-ontology.owl", "xml"),
        (corpora / "ontology" / "sdg-ontology-comprehensive.owl", "xml"),
        (corpora / "ontology" / "sdg-vocab.ttl", "turtle"),
    ]
    skos_files = [
        corpora / "vocabulary" / "vocabulary.ttl",
        corpora / "vocabulary" / "domain-concepts.ttl",
    ]
    by_id: dict[str, str] = {}

    def keep(node: dict) -> None:
        node_id = str(node.get("id") or "")
        if not node_id:
            return
        text = embedding_text(node)
        previous = by_id.get(node_id)
        if previous is None or len(text) > len(previous):
            by_id[node_id] = text

    ingestor = OntologyIngestor()
    for path, fmt in owl_files:
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"ingest {path.name}", flush=True)
        data = ingestor.ingest_ontology(path, format=fmt)
        nodes, _edges = _convert_ontology_to_graph(data.data)
        for node in nodes:
            keep(node)
        print(f"  nodes {len(nodes)} unique {len(by_id)}", flush=True)

    for path in skos_files:
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"skos {path.name}", flush=True)
        nodes, _edges = parse_skos_file(path.read_bytes(), "turtle")
        for node in nodes:
            keep(node)
        print(f"  nodes {len(nodes)} unique {len(by_id)}", flush=True)

    return sorted(by_id.items())


def _nvidia_rows(query: str) -> list[list[str]] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-{query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line:
            rows.append([part.strip() for part in line.split(",")])
    return rows


def busy_gpus(wanted: list[int]) -> list[str]:
    """Compute apps holding at least 512 MiB on a requested GPU.

    Same occupancy probe Aegir's gpu_guard uses. nvidia-smi is the truth.
    """
    apps = _nvidia_rows("compute-apps=pid,used_memory,gpu_uuid")
    uuids = _nvidia_rows("gpu=index,uuid")
    if apps is None or uuids is None:
        return ["nvidia-smi did not answer; refusing to encode"]
    index_by_uuid = {}
    for row in uuids:
        if len(row) >= 2:
            try:
                index_by_uuid[row[1]] = int(row[0])
            except ValueError:
                continue
    wanted_set = set(wanted)
    found = []
    for row in apps:
        if len(row) < 3:
            continue
        try:
            pid = int(row[0])
            used = int(float(row[1]))
        except ValueError:
            continue
        gpu = index_by_uuid.get(row[2])
        if gpu in wanted_set and used >= _MIN_BUSY_MIB:
            found.append(f"GPU {gpu} pid {pid} {used} MiB")
    return found


def _cuda_libs() -> str | None:
    override = os.environ.get("SEMANTICA_CUDA_DRIVER_LIBS")
    path = Path(override) if override else _DEFAULT_CUDA_LIBS
    if path.is_dir() and (path / "libcuda.so.1").is_file():
        return str(path)
    return None


def _worker_env(gpu: int) -> dict[str, str]:
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    env["HF_HOME"] = env.get("HF_HOME") or "/raid/cache/huggingface"
    env["TOKENIZERS_PARALLELISM"] = "false"
    # The embed interpreter is a standalone CPython linked to the system glibc.
    # A devenv LD_LIBRARY_PATH carries a Nix libstdc++ that asks for GLIBC_2.38
    # and then numpy fails to import. Torch finds its own CUDA libs via RPATH.
    # Keep only the driver directory Aegir uses so libcuda resolves.
    libs = _cuda_libs()
    if libs:
        env["LD_LIBRARY_PATH"] = libs
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _load_cache_constants():
    """Load the archive module by file path so the GPU venv skips package init."""
    import importlib.util

    path = _REPO / "semantica" / "explorer" / "embedding_cache.py"
    spec = importlib.util.spec_from_file_location("_semantica_embedding_cache", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_worker(input_path: Path, output_path: Path, batch_size: int) -> None:
    """Encode one shard. CUDA_VISIBLE_DEVICES is already a single GPU."""
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    constants = _load_cache_constants()
    dim = constants.DIM
    model_id = constants.MODEL_ID
    prompt_name = constants.PROMPT_NAME

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise SystemExit(
            f"CUDA is not visible in this process "
            f"(available={torch.cuda.is_available()} devices={torch.cuda.device_count()}). "
            "Prepend the driver libs the way Aegir does "
            f"(SEMANTICA_CUDA_DRIVER_LIBS, default {_DEFAULT_CUDA_LIBS})."
        )
    texts = []
    with input_path.open(encoding="utf-8") as handle:
        for line in handle:
            texts.append(json.loads(line)["text"])
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
    if dtype == torch.float16:
        raise SystemExit("refusing float16; EmbeddingGemma activations overflow")
    print(
        f"gpu {os.environ.get('CUDA_VISIBLE_DEVICES')} dtype {dtype} texts {len(texts)}",
        flush=True,
    )
    model = SentenceTransformer(
        model_id,
        device="cuda",
        model_kwargs={"torch_dtype": dtype},
        config_kwargs={"vision_config": None, "audio_config": None},
    )
    vectors = model.encode(
        texts,
        prompt_name=prompt_name,
        normalize_embeddings=True,
        batch_size=batch_size,
        show_progress_bar=True,
    )
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.shape != (len(texts), dim):
        raise SystemExit(f"expected {(len(texts), dim)}, got {matrix.shape}")
    if not np.isfinite(matrix).all():
        raise SystemExit("embedding matrix has non-finite values")
    np.save(output_path, matrix)
    print(f"wrote {output_path} {matrix.shape}", flush=True)


def _shards(records: list[tuple[str, str]], gpus: list[int], directory: Path):
    shards = []
    for index, gpu in enumerate(gpus):
        chunk = records[index::len(gpus)]
        path = directory / f"shard-{gpu}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for _node_id, text in chunk:
                handle.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
        shards.append((gpu, chunk, path, directory / f"shard-{gpu}.npy"))
    return shards


def encode(records: list[tuple[str, str]], args: argparse.Namespace) -> None:
    import numpy as np

    from semantica.explorer.embedding_cache import DIM, MODEL_ID, PROMPT_NAME, write_cache

    python = args.python
    if not python.is_file():
        raise SystemExit(
            f"embed interpreter missing: {python}\n"
            "Create it with the uv commands at the top of scripts/embed_sdg_gemma.py."
        )
    if _cuda_libs() is None:
        raise SystemExit(
            "CUDA driver libs not found. Set SEMANTICA_CUDA_DRIVER_LIBS to a directory "
            f"that contains libcuda.so.1 (Aegir keeps them in {_DEFAULT_CUDA_LIBS})."
        )
    held = busy_gpus(args.gpus)
    if held:
        raise SystemExit(
            "refusing to encode while compute processes hold the requested GPUs:\n  "
            + "\n  ".join(held)
        )

    with tempfile.TemporaryDirectory(prefix="semantica-embed-") as tmp:
        directory = Path(tmp)
        shards = _shards(records, args.gpus, directory)
        procs: list[tuple[int, subprocess.Popen, Path, object]] = []
        try:
            for gpu, chunk, src, dest in shards:
                if not chunk:
                    continue
                env = _worker_env(gpu)
                log_path = directory / f"gpu-{gpu}.log"
                log = log_path.open("w", encoding="utf-8")
                proc = subprocess.Popen(
                    [
                        str(python),
                        str(Path(__file__).resolve()),
                        "--worker",
                        str(src),
                        str(dest),
                        "--batch-size",
                        str(args.batch_size),
                    ],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                procs.append((gpu, proc, log_path, log))
                print(f"spawned gpu {gpu} pid {proc.pid} texts {len(chunk)}", flush=True)
            failed = []
            for gpu, proc, log_path, log in procs:
                code = proc.wait()
                log.close()
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                print(f"gpu {gpu} exit {code}\n{tail}", flush=True)
                if code != 0:
                    failed.append(gpu)
            if failed:
                raise SystemExit(f"workers failed on GPUs {failed}")
        finally:
            for _gpu, proc, _log_path, log in procs:
                if proc.poll() is None:
                    proc.terminate()
                log.close()

        parts = []
        ids: list[str] = []
        for gpu, chunk, _src, dest in shards:
            if not chunk:
                continue
            matrix = np.load(dest)
            if matrix.shape != (len(chunk), DIM):
                raise SystemExit(f"GPU {gpu} wrote {matrix.shape}, expected {(len(chunk), DIM)}")
            parts.append(matrix)
            ids.extend(node_id for node_id, _text in chunk)
        vectors = np.concatenate(parts, axis=0)
        write_cache(
            args.out,
            ids,
            vectors,
            {
                "model": MODEL_ID,
                "prompt": PROMPT_NAME,
                "dim": DIM,
                "gpus": args.gpus,
                "text": "label, comment when it differs, stored alt labels",
            },
        )
    print(f"wrote {args.out} vectors {vectors.shape}", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.worker:
        run_worker(Path(args.worker[0]), Path(args.worker[1]), args.batch_size)
        return
    records = collect_nodes(args.corpora)
    print(f"unique IRIs {len(records)}", flush=True)
    if args.dry_run:
        return
    encode(records, args)


if __name__ == "__main__":
    main()
