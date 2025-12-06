from __future__ import annotations
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .base import MetadataExtractor

logger = logging.getLogger(__name__)


# =============================
# Helper data structures (internal)
# =============================
@dataclass
class _Pin:
    node_id: str
    slot: str


@dataclass
class _Edge:
    src: _Pin
    dst: _Pin
    etype: Optional[str] = None


@dataclass
class _Node:
    id: str
    type: str
    inputs: Dict[str, Any]  # canonicalized name->info (may be empty if unknown)
    widgets: Dict[str, Any]  # canonicalized widget-name -> value
    title: Optional[str] = None
    raw: Optional[Dict[str, Any]] = (
        None  # original node JSON (optional for advanced heuristics)
    )


class _Graph:
    def __init__(self, nodes: Dict[str, _Node], edges: List[_Edge]):
        self.nodes = nodes
        self.edges = edges
        self._in: Dict[str, List[_Edge]] = {}
        self._out: Dict[str, List[_Edge]] = {}
        for e in edges:
            self._out.setdefault(e.src.node_id, []).append(e)
            self._in.setdefault(e.dst.node_id, []).append(e)

    def inputs(self, node_id: str) -> List[_Edge]:
        return self._in.get(node_id, [])

    def outputs(self, node_id: str) -> List[_Edge]:
        return self._out.get(node_id, [])

    def induced(self, node_ids: Iterable[str]) -> "_Graph":
        nid = set(node_ids)
        nodes = {k: v for k, v in self.nodes.items() if k in nid}
        edges = [e for e in self.edges if e.src.node_id in nid and e.dst.node_id in nid]
        return _Graph(nodes, edges)


# Heuristic typing by destination slot name
_ETYPES_BY_SLOT = {
    "model": "model",
    "clip": "clip",
    "vae": "vae",
    "conditioning": "conditioning",
    "positive": "conditioning",
    "negative": "conditioning",
    "latent": "latent",
    "image": "image",
    "video": "video",
}


def _infer_edge_type(dst_slot: str) -> Optional[str]:
    key = dst_slot.lower()
    for k, v in _ETYPES_BY_SLOT.items():
        if k in key:
            return v
    return None


def _coerce_str(v: Any) -> str:
    try:
        return str(v)
    except Exception:
        return ""


def _as_str_name(x: Any, fallback: str) -> str:
    if isinstance(x, dict):
        return _coerce_str(x.get("name") or x.get("label") or fallback)
    return _coerce_str(x) or fallback


def _canonicalize_inputs(n_inputs: Any) -> Dict[str, Any]:
    """Return a name->info mapping for inputs, handling both 'prompt' and 'workflow' styles."""
    if isinstance(n_inputs, dict):
        return dict(n_inputs)
    if isinstance(n_inputs, list):
        out: Dict[str, Any] = {}
        for i, item in enumerate(n_inputs):
            name = _as_str_name(item, f"in{i}")
            out[name] = item
        return out
    return {}


def _node_widgets(
    n: Dict[str, Any], widget_idx_map: Optional[Dict[str, Dict[str, int]]] = None
) -> Dict[str, Any]:
    widgets: Dict[str, Any] = {}
    wvals = n.get("widgets_values")
    # Common shapes: dict (good), list (needs names), None
    if isinstance(wvals, dict):
        widgets.update(wvals)
    elif isinstance(wvals, list):
        # If a 'widgets' descriptor list exists, map names->values
        if isinstance(n.get("widgets"), list):
            for i, wdesc in enumerate(n.get("widgets", [])):
                label = _as_str_name(wdesc, f"w{i}")
                if i < len(wvals):
                    widgets[label] = wvals[i]
        else:
            # Fallback to w{i}
            for i, val in enumerate(wvals):
                widgets[f"w{i}"] = val
    # Promote known fields that sometimes live at top-level
    for k in (
        "seed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "denoise",
        "width",
        "height",
        "text",
        "filename",
        "fps",
        "frame_rate",
    ):
        if k in n:
            widgets.setdefault(k, n[k])
    # Apply widget_idx_map (workflow-level mapping) if present
    if widget_idx_map is not None:
        try:
            nid = str(n.get("id") or n.get("_id") or n.get("uid") or n.get("name"))
            mapping = widget_idx_map.get(nid)
            if isinstance(mapping, dict) and isinstance(wvals, list):
                for key, idx in mapping.items():
                    if isinstance(idx, int) and 0 <= idx < len(wvals):
                        widgets[key] = wvals[idx]
        except Exception:
            pass
    return widgets


def _build_graph(workflow: Dict[str, Any]) -> _Graph:
    """Build a typed graph from ComfyUI JSON.

    Supports both legacy 'prompt' format (dict keyed by node-id strings) and
    modern 'workflow' (LiteGraph) format with 'nodes': [...] and 'links': [...].
    """
    nodes: Dict[str, _Node] = {}
    edges: List[_Edge] = []

    widget_idx_map = (
        workflow.get("widget_idx_map") if isinstance(workflow, dict) else None
    )

    # 1) Determine node listing
    if isinstance(workflow.get("nodes"), list):
        raw_nodes: List[Dict[str, Any]] = workflow["nodes"]
        style = "workflow"
    else:
        raw_nodes = (
            list(workflow.values())
            if isinstance(workflow, dict)
            and all(isinstance(v, dict) for v in workflow.values())
            else []
        )
        style = "prompt"

    # 2) Build node objects
    for n in raw_nodes:
        nid = str(n.get("id") or n.get("_id") or n.get("uid") or n.get("name"))
        ntype = str(n.get("type") or n.get("class_type") or "Unknown")
        widgets = _node_widgets(n, widget_idx_map if style == "workflow" else None)
        inputs_map = _canonicalize_inputs(n.get("inputs") or n.get("input") or {})
        nodes[nid] = _Node(
            id=nid,
            type=ntype,
            inputs=inputs_map,
            widgets=widgets,
            title=n.get("title"),
            raw=n,
        )

    # 3) Build edges
    if style == "workflow":
        # Prefer explicit 'links' list when present
        links = workflow.get("links") or []
        if isinstance(links, list):
            for l in links:
                # Typical shapes: [id, src_id, src_slot_index, dst_id, dst_slot_index, label?]
                if not isinstance(l, list) or len(l) < 5:
                    continue
                _, src_id, src_slot_idx, dst_id, dst_slot_idx, *rest = l
                src_id = str(src_id)
                dst_id = str(dst_id)
                src_slot_name = None
                dst_slot_name = None
                # Try to resolve names from node port arrays
                try:
                    if src_id in nodes:
                        src_raw = nodes[src_id].raw
                        if src_raw is not None:
                            outs = src_raw.get("outputs") or []
                            if (
                                isinstance(outs, list)
                                and isinstance(src_slot_idx, int)
                                and 0 <= src_slot_idx < len(outs)
                            ):
                                src_slot_name = _as_str_name(
                                    outs[src_slot_idx], str(src_slot_idx)
                                )
                except Exception:
                    pass
                try:
                    if dst_id in nodes:
                        dst_raw = nodes[dst_id].raw
                        if dst_raw is not None:
                            ins = dst_raw.get("inputs") or []
                            if (
                                isinstance(ins, list)
                                and isinstance(dst_slot_idx, int)
                                and 0 <= dst_slot_idx < len(ins)
                            ):
                                dst_slot_name = _as_str_name(
                                    ins[dst_slot_idx], str(dst_slot_idx)
                                )
                except Exception:
                    pass
                # Some workflows put the dest slot name as the final element
                if not dst_slot_name and rest and isinstance(rest[-1], str):
                    dst_slot_name = rest[-1]
                if src_slot_name is None:
                    src_slot_name = str(src_slot_idx)
                if dst_slot_name is None:
                    dst_slot_name = str(dst_slot_idx)
                edges.append(
                    _Edge(
                        src=_Pin(src_id, src_slot_name),
                        dst=_Pin(dst_id, dst_slot_name),
                        etype=_infer_edge_type(dst_slot_name),
                    )
                )
        else:
            # Fallback: synthesize edges via per-node 'inputs' lists if they embed link ids
            for n in raw_nodes:
                dst_id = str(
                    n.get("id") or n.get("_id") or n.get("uid") or n.get("name")
                )
                ins = n.get("inputs") or []
                if not isinstance(ins, list):
                    continue
                for i, inp in enumerate(ins):
                    link_id = inp.get("link") if isinstance(inp, dict) else None
                    if link_id is None:
                        continue
                    # Without global link table we cannot resolve src; skip
                    # but at least capture slot name for typing
                    dst_slot_name = _as_str_name(inp, str(i))
                    edges.append(
                        _Edge(
                            src=_Pin("?", "?"),
                            dst=_Pin(dst_id, dst_slot_name),
                            etype=_infer_edge_type(dst_slot_name),
                        )
                    )
    else:
        # 'prompt' style: each node has inputs as dict mapping slot-> [src_id, src_slot]
        for n in raw_nodes:
            nid = str(n.get("id") or n.get("_id") or n.get("uid") or n.get("name"))
            inputs = n.get("inputs") or {}
            if isinstance(inputs, dict):
                for slot, conn in inputs.items():
                    # conn could be [src_id, src_slot] or list of those
                    conns = (
                        conn
                        if (
                            isinstance(conn, list)
                            and conn
                            and isinstance(conn[0], list)
                        )
                        else [conn]
                    )
                    for c in conns:
                        if isinstance(c, list) and len(c) >= 2:
                            src_id = str(c[0])
                            src_slot = _coerce_str(c[1])
                            edges.append(
                                _Edge(
                                    src=_Pin(src_id, src_slot),
                                    dst=_Pin(nid, str(slot)),
                                    etype=_infer_edge_type(str(slot)),
                                )
                            )

    return _Graph(nodes, edges)


def _find_terminals(g: _Graph) -> List[str]:
    out = []
    for nid, node in g.nodes.items():
        nt = node.type.lower()
        if any(
            t in nt
            for t in [
                "saveimage",
                "imagesave",
                "previewimage",
                "savevideo",
                "videosave",
                "videocombine",
            ]
        ):
            out.append(nid)
    return out


def _backward_slice(g: _Graph, terminal_id: str) -> _Graph:
    stack = [terminal_id]
    keep, seen = set(), set()
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        keep.add(nid)
        for e in g.inputs(nid):
            stack.append(e.src.node_id)
    return g.induced(keep)


def _topo_order(g: _Graph) -> List[str]:
    indeg = {nid: 0 for nid in g.nodes}
    for e in g.edges:
        if e.src.node_id in g.nodes and e.dst.node_id in g.nodes:
            indeg[e.dst.node_id] += 1
    q = [nid for nid, d in indeg.items() if d == 0]
    order: List[str] = []
    while q:
        nid = q.pop()
        order.append(nid)
        for e in g.outputs(nid):
            if e.dst.node_id in indeg:
                indeg[e.dst.node_id] -= 1
                if indeg[e.dst.node_id] == 0:
                    q.append(e.dst.node_id)
    for nid in g.nodes:
        if nid not in order:
            order.append(nid)
    return order


# =============================
# ComfyUI Extractor (no sidecar dependency)
# =============================
class ComfyUIMetadataExtractor(MetadataExtractor):
    """Extracts standardized AI generation metadata from ComfyUI-generated media.

    This version does **not** depend on any sidecar JSON files. It looks for embedded
    metadata only:
      - PNG/WebP text chunks (keys like 'prompt' or 'workflow')
      - JPEG EXIF UserComment (rare)
      - Video container tags for MP4/WEBM/MOV/MKV (via ffprobe/PyAV if available)

    If no embedded ComfyUI workflow JSON is found, extraction returns None.
    Video fps/frames/duration are still probed (best-effort) when available.
    """

    VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}
    IMAGE_EXTS = {".png", ".webp", ".jpg", ".jpeg"}

    # ------ Public API ------
    def can_extract(self, media_path: Path) -> bool:
        return False  # disable

        try:
            suffix = media_path.suffix.lower()
            if suffix in {".png", ".webp"}:
                md = self._get_png_metadata(media_path)
                for k, v in md.items():
                    lk = str(k).lower()
                    sv = (
                        v.decode("utf-8", "ignore")
                        if isinstance(v, (bytes, bytearray))
                        else str(v)
                    )
                    if any(sig in lk for sig in ("prompt", "workflow")) and (
                        "{" in sv and "nodes" in sv or '"class_type"' in sv
                    ):
                        return True
            if suffix in {".jpg", ".jpeg"}:
                exif = self._get_exif_metadata(media_path)
                uc = exif.get("UserComment")
                if isinstance(uc, str) and ("nodes" in uc and "{" in uc):
                    return True
            if suffix in self.VIDEO_EXTS:
                payload = self._probe_video_for_comfy_payload(media_path)
                return payload is not None
        except Exception:
            return False
        return False

    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
        parsing_errors: List[Dict[str, Any]] = []
        try:
            payload, raw_meta = self._load_comfy_payload(media_path, parsing_errors)
            if not payload:
                return None

            graph = _build_graph(payload)
            terminals = _find_terminals(graph)
            term = self._select_terminal(terminals, graph, media_path)
            sub = _backward_slice(graph, term) if term else graph

            # Collect fields in a simple state dict
            state: Dict[str, Any] = {
                "positive": [],
                "negative": [],
                "model": None,
                "sampler": None,
                "scheduler": None,
                "steps": None,
                "cfg_scale": None,
                "seed": None,
                "width": None,
                "height": None,
                "batch_size": None,
                "clip_skip": None,
                "loras": [],
                "frame_rate": None,
                "video_length": None,
            }

            for nid in _topo_order(sub):
                node = sub.nodes[nid]
                try:
                    self._apply_known_adapters(node, sub, state)
                except Exception as e:
                    parsing_errors.append(
                        {
                            "error_type": type(e).__name__,
                            "error_message": f"Adapter failed for node {node.type} ({node.id}): {e}",
                            "raw_data": str(node.widgets)[:2000],
                        }
                    )
                    self._heuristic_fallback(node, sub, state)

            # Probe container for video facts (fps/frames/duration) whether or not we found JSON
            if media_path.suffix.lower() in self.VIDEO_EXTS:
                vmeta = self._probe_video_metadata(media_path)
                if vmeta.get("fps") and not state.get("frame_rate"):
                    state["frame_rate"] = vmeta["fps"]
                if vmeta.get("frames") and not state.get("video_length"):
                    state["video_length"] = vmeta["frames"]
                raw_meta.setdefault("video_probe", {}).update(vmeta)

            duration = None
            if state.get("frame_rate") and state.get("video_length"):
                try:
                    fps = float(state["frame_rate"]) or 0.0
                    duration = (int(state["video_length"]) / fps) if fps > 0 else None
                except Exception:
                    duration = None

            result: Dict[str, Any] = {
                "source": "ComfyUI",
                "raw_metadata": raw_meta,
                # Prompts
                "prompt": self._join_prompts(state.get("positive") or []),
                "negative_prompt": self._join_prompts(state.get("negative") or []),
                # Core
                "model": state.get("model"),
                "sampler": state.get("sampler"),
                "scheduler": state.get("scheduler"),
                "steps": self._safe_int(state.get("steps")),
                "cfg_scale": self._safe_float(state.get("cfg_scale")),
                "seed": self._safe_int(state.get("seed")),
                # Video
                "frame_rate": self._safe_float(state.get("frame_rate")),
                "duration": duration,
                "video_length": self._safe_int(state.get("video_length")),
                # LoRAs
                "loras": state.get("loras") or [],
                # Additional
                "width": self._safe_int(state.get("width")),
                "height": self._safe_int(state.get("height")),
                "batch_size": self._safe_int(state.get("batch_size")),
                "clip_skip": self._safe_int(state.get("clip_skip")),
                "tags": [],
                "parsing_errors": parsing_errors,
            }
            return result
        except Exception as e:
            logger.error(f"ComfyUI extract failed for {media_path}: {e}")
            return None

    # ------ Internal helpers ------
    def _load_comfy_payload(
        self, media_path: Path, parsing_errors: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Load embedded ComfyUI workflow JSON from image/video. No sidecar usage."""
        raw_meta: Dict[str, Any] = {"source_path": str(media_path)}
        payload: Optional[Dict[str, Any]] = None

        try:
            if media_path.suffix.lower() in {".png", ".webp"}:
                png_md = self._get_png_metadata(media_path)
                if png_md:
                    raw_meta["png_text"] = {
                        k: (
                            v.decode("utf-8", "ignore")
                            if isinstance(v, (bytes, bytearray))
                            else v
                        )
                        for k, v in png_md.items()
                    }
                    # Prefer 'workflow' over 'prompt' when both exist, since it includes UI port metadata
                    ordered_keys = [k for k in ("workflow", "prompt") if k in png_md]
                    for key in ordered_keys:
                        s = png_md[key]
                        if isinstance(s, (bytes, bytearray)):
                            s = s.decode("utf-8", errors="ignore")
                        try:
                            payload = json.loads(s)
                            break
                        except json.JSONDecodeError:
                            try:
                                payload = json.loads(json.loads(s))
                                break
                            except Exception as e:
                                parsing_errors.append(
                                    {
                                        "error_type": type(e).__name__,
                                        "error_message": str(e),
                                        "raw_data": str(s)[:2000],
                                    }
                                )
        except Exception as e:
            parsing_errors.append(
                {
                    "error_type": type(e).__name__,
                    "error_message": f"PNG read failed: {e}",
                    "raw_data": "<binary>",
                }
            )

        if payload is None and media_path.suffix.lower() in {".jpg", ".jpeg"}:
            try:
                exif_md = self._get_exif_metadata(media_path)
                raw_meta["exif"] = {
                    k: (v if isinstance(v, (str, int, float)) else str(v))
                    for k, v in exif_md.items()
                }
                uc = exif_md.get("UserComment")
                if isinstance(uc, str) and ("{" in uc and "nodes" in uc):
                    try:
                        payload = json.loads(uc)
                    except Exception as e:
                        parsing_errors.append(
                            {
                                "error_type": type(e).__name__,
                                "error_message": str(e),
                                "raw_data": uc[:2000],
                            }
                        )
            except Exception as e:
                parsing_errors.append(
                    {
                        "error_type": type(e).__name__,
                        "error_message": f"EXIF read failed: {e}",
                        "raw_data": "<binary>",
                    }
                )

        if payload is None and media_path.suffix.lower() in self.VIDEO_EXTS:
            try:
                payload, tags_meta = self._probe_video_for_comfy_payload(
                    media_path, return_tags=True
                )
                if tags_meta:
                    raw_meta["video_tags"] = tags_meta
            except Exception as e:
                parsing_errors.append(
                    {
                        "error_type": type(e).__name__,
                        "error_message": f"Video tag probe failed: {e}",
                        "raw_data": str(media_path),
                    }
                )

        return payload, raw_meta

    def _select_terminal(
        self, terminals: List[str], graph: _Graph, media_path: Path
    ) -> Optional[str]:
        if not terminals:
            return None
        if len(terminals) == 1:
            return terminals[0]
        base = media_path.stem.lower()
        for tid in terminals:
            node = graph.nodes[tid]
            w = {k.lower(): v for k, v in node.widgets.items()}
            for key in ("filename", "file", "name", "pattern", "w0"):
                if key in w and isinstance(w[key], str) and base in w[key].lower():
                    return tid
        for tid in terminals:
            if any(
                x in graph.nodes[tid].type.lower()
                for x in ("saveimage", "savevideo", "videocombine")
            ):
                return tid
        return terminals[0]

    def _apply_known_adapters(
        self, node: _Node, sub: _Graph, state: Dict[str, Any]
    ) -> None:
        ntype = node.type.lower()
        # Checkpoint / Model loader
        if "checkpoint" in ntype or (
            "model" in node.widgets
            and any(
                k in node.widgets
                for k in ("ckpt_name", "model", "model_name", "file", "w0")
            )
        ):
            model = self._first_string(
                node.widgets, ["ckpt_name", "model", "model_name", "file", "w0"]
            ) or self._first_path_like(node.widgets)
            if model:
                state["model"] = Path(str(model)).name
            return
        # CLIP text encode
        if "cliptextencode" in ntype or (
            "text" in node.widgets and "clip" in node.inputs
        ):
            txt = self._first_string(node.widgets, ["text"]) or self._first_text_like(
                node.widgets
            )
            if txt:
                roles = [e.dst.slot.lower() for e in sub.outputs(node.id)]
                bucket = "negative" if any("neg" in r for r in roles) else "positive"
                state[bucket].append(str(txt))
            return
        # Sampler
        if "sampler" in ntype or "ksampler" in ntype:
            w = node.widgets
            # Prefer explicit names, then fall back to common indices
            state["seed"] = self._prefer(
                state.get("seed"), self._to_int(w.get("seed") or w.get("w0"))
            )
            state["steps"] = self._prefer(
                state.get("steps"), self._to_int(w.get("steps") or w.get("w2"))
            )
            state["cfg_scale"] = self._prefer(
                state.get("cfg_scale"), self._to_float(w.get("cfg") or w.get("w3"))
            )
            state["sampler"] = self._prefer(
                state.get("sampler"), self._to_str(w.get("sampler_name") or w.get("w4"))
            )
            state["scheduler"] = self._prefer(
                state.get("scheduler"), self._to_str(w.get("scheduler") or w.get("w5"))
            )
            return
        # Latent init / base size
        if (
            "emptylatentimage" in ntype
            or "latents" in ntype
            or ("width" in node.widgets and "height" in node.widgets)
        ):
            w = node.widgets
            state["width"] = self._prefer(
                state.get("width"), self._to_int(w.get("width") or w.get("w0"))
            )
            state["height"] = self._prefer(
                state.get("height"), self._to_int(w.get("height") or w.get("w1"))
            )
            if "batch" in w or "batch_size" in w or "w2" in w:
                state["batch_size"] = self._prefer(
                    state.get("batch_size"),
                    self._to_int(w.get("batch") or w.get("batch_size") or w.get("w2")),
                )
            return
        # VAE clip_skip sometimes exposed as widget
        if "vae" in ntype and "loader" in ntype:
            if "clip_skip" in node.widgets:
                state["clip_skip"] = self._prefer(
                    state.get("clip_skip"), self._to_int(node.widgets.get("clip_skip"))
                )
            return
        # LoRA loader / applier — include special casing for Power Lora Loader (rgthree)
        if "lora" in ntype:
            # Special: rgthree Power Lora Loader packs enabled entries in widgets_values (list of dicts)
            added = False
            for v in node.widgets.values():
                if isinstance(v, list):
                    for item in v:
                        if (
                            isinstance(item, dict)
                            and item.get("on")
                            and item.get("lora")
                        ):
                            name = Path(str(item.get("lora"))).name
                            weight = self._to_float(item.get("strength")) or 1.0
                            state.setdefault("loras", []).append(
                                {"lora_name": name, "lora_weight": weight}
                            )
                            added = True
            if not added:
                name = (
                    self._first_path_like(node.widgets)
                    or self._first_string(node.widgets, ["lora", "file", "w0"])
                    or "lora"
                )
                # Prefer UNet weight keys, then text weight
                unet_w = self._to_float(
                    self._first_number_like(
                        node.widgets,
                        ["strength_model", "unet_weight", "strength", "w1"],
                    )
                )
                text_w = self._to_float(
                    self._first_number_like(
                        node.widgets, ["strength_clip", "text_weight", "w2"]
                    )
                )
                weight = (
                    unet_w
                    if unet_w is not None
                    else (text_w if text_w is not None else 1.0)
                )
                state.setdefault("loras", []).append(
                    {
                        "lora_name": Path(str(name)).name,
                        "lora_weight": float(weight) if weight is not None else 1.0,
                    }
                )
            return
        # Resize/upscale nodes may update effective size (schema stores a single width/height)
        if any(k in ntype for k in ("scale", "resize", "upscale")):
            if "width" in node.widgets and "height" in node.widgets:
                state["width"] = self._prefer(
                    state.get("width"), self._to_int(node.widgets.get("width"))
                )
                state["height"] = self._prefer(
                    state.get("height"), self._to_int(node.widgets.get("height"))
                )
            return
        # Video save / combine (fps/frame_rate)
        if any(k in ntype for k in ("savevideo", "videosave", "videocombine")):
            if "fps" in node.widgets:
                state["frame_rate"] = self._prefer(
                    state.get("frame_rate"), self._to_float(node.widgets.get("fps"))
                )
            if "frame_rate" in node.widgets:
                state["frame_rate"] = self._prefer(
                    state.get("frame_rate"),
                    self._to_float(node.widgets.get("frame_rate")),
                )
            # Some nodes stash preview params under nested dicts
            try:
                preview = node.widgets.get("videopreview") or {}
                params = preview.get("params") if isinstance(preview, dict) else {}
                fr = params.get("frame_rate") if isinstance(params, dict) else None
                if fr is not None:
                    state["frame_rate"] = self._prefer(
                        state.get("frame_rate"), self._to_float(fr)
                    )
            except Exception:
                pass
            return

        # Unknown: heuristic fallthrough
        self._heuristic_fallback(node, sub, state)

    def _heuristic_fallback(
        self, node: _Node, sub: _Graph, state: Dict[str, Any]
    ) -> None:
        w = node.widgets
        # Prompts (avoid storing huge binary blobs masquerading as strings)
        txt = self._first_text_like(w)
        if txt and len(txt) < 8000:
            roles = [e.dst.slot.lower() for e in sub.outputs(node.id)]
            bucket = "negative" if any("neg" in r for r in roles) else "positive"
            state[bucket].append(str(txt))
        # Sampler-ish values
        for k, target in [
            ("seed", "seed"),
            ("steps", "steps"),
            ("cfg", "cfg_scale"),
            ("sampler", "sampler"),
            ("sampler_name", "sampler"),
            ("scheduler", "scheduler"),
        ]:
            if k in w and state.get(target) is None:
                val = w[k]
                if target in ("seed", "steps"):
                    state[target] = self._to_int(val)
                elif target == "cfg_scale":
                    state[target] = self._to_float(val)
                else:
                    state[target] = self._to_str(val)
        # Size
        if state.get("width") is None and "width" in w:
            state["width"] = self._to_int(w.get("width"))
        if state.get("height") is None and "height" in w:
            state["height"] = self._to_int(w.get("height"))
        # LoRA guess
        for key, val in w.items():
            if (
                isinstance(val, str)
                and ("lora" in val.lower())
                and (val.endswith((".safetensors", ".pt")))
            ):
                state.setdefault("loras", []).append(
                    {"lora_name": Path(val).name, "lora_weight": 1.0}
                )
                break

    # ---- tiny conversion helpers ----
    def _prefer(self, old, new):
        return old if old not in (None, "") else new

    def _to_int(self, v) -> Optional[int]:
        return self._safe_int(v)

    def _to_float(self, v) -> Optional[float]:
        return self._safe_float(v)

    def _to_str(self, v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    def _first_string(self, d: Dict[str, Any], keys: List[str]) -> Optional[str]:
        for k in keys:
            if k in d and isinstance(d[k], str) and d[k].strip():
                return str(d[k])
        return None

    def _first_number_like(self, d: Dict[str, Any], keys: List[str]) -> Optional[float]:
        for k in keys:
            if k in d:
                try:
                    return float(d[k])
                except Exception:
                    continue
        return None

    def _first_text_like(self, d: Dict[str, Any]) -> Optional[str]:
        # Try common textual widget keys first
        for key in ("text", "prompt", "string", "caption"):
            value = d.get(key)
            if isinstance(value, str) and value.strip():
                return str(value)
        # Then any reasonable-size string value
        for k, v in d.items():
            if isinstance(v, str) and 0 < len(v) < 4000:
                return v
        return None

    def _first_path_like(self, d: Dict[str, Any]) -> Optional[str]:
        for v in d.values():
            if isinstance(v, str) and (v.endswith((".safetensors", ".ckpt", ".pt"))):
                return v
        return None

    def _join_prompts(self, parts: List[str]) -> Optional[str]:
        parts = [p for p in (parts or []) if isinstance(p, str) and p.strip()]
        if not parts:
            return None
        return "\n".join(parts)

    # ---- Video tag probing (embedded text -> workflow JSON) ----
    def _probe_video_for_comfy_payload(
        self, media_path: Path, return_tags: bool = False
    ):
        """Attempt to read ComfyUI workflow JSON from video container tags.
        Returns payload (and tags dict if return_tags) or None.
        """
        tags_meta: Dict[str, Any] = {}
        # 1) ffprobe
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format:stream_tags",
                "-show_format",
                "-print_format",
                "json",
                str(media_path),
            ]
            data = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5)
            j = json.loads(data.decode("utf-8", errors="ignore"))
            # Aggregate tags from format + streams
            all_tags: Dict[str, Any] = {}
            if isinstance(j.get("format", {}).get("tags"), dict):
                all_tags.update(j["format"]["tags"])
            for s in j.get("streams", []) or []:
                if isinstance(s.get("tags"), dict):
                    for k, v in s["tags"].items():
                        all_tags.setdefault(k, v)
            tags_meta["ffprobe"] = all_tags
            payload = self._json_from_tag_dict(all_tags)
            if payload is not None:
                return (payload, tags_meta) if return_tags else payload
        except Exception:
            pass
        # 2) PyAV
        try:
            import av  # type: ignore

            with av.open(str(media_path)) as container:
                meta = dict(getattr(container, "metadata", {}) or {})
                tags_meta["pyav_container"] = meta
                payload = self._json_from_tag_dict(meta)
                if payload is not None:
                    return (payload, tags_meta) if return_tags else payload
                # also try first video stream
                vstreams = [s for s in container.streams if s.type == "video"]
                if vstreams:
                    s0 = vstreams[0]
                    smeta = dict(getattr(s0, "metadata", {}) or {})
                    tags_meta["pyav_stream"] = smeta
                    payload = self._json_from_tag_dict(smeta)
                    if payload is not None:
                        return (payload, tags_meta) if return_tags else payload
        except Exception:
            pass
        return (None, tags_meta) if return_tags else None

    def _json_from_tag_dict(self, tags: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Search common tag keys
        candidates: List[str] = []
        for k, v in (tags or {}).items():
            if v is None:
                continue
            s = v if isinstance(v, str) else str(v)
            lk = k.lower()
            if lk in (
                "prompt",
                "workflow",
                "description",
                "comment",
                "comfyui_prompt",
                "comfyui_workflow",
            ) or ("prompt" in lk or "workflow" in lk):
                candidates.append(s)
        # Also scan all values for JSON with 'nodes' or legacy dict
        for v in list((tags or {}).values()) + candidates:
            if not isinstance(v, str):
                continue
            s = v
            if "{" in s and ("nodes" in s or "class_type" in s):
                for attempt in (s, self._try_json_string(s)):
                    try:
                        j = json.loads(attempt) if isinstance(attempt, str) else attempt
                        if isinstance(j, dict):
                            if "nodes" in j or any(
                                isinstance(val, dict) and "class_type" in val
                                for val in j.values()
                            ):
                                if "nodes" in j:
                                    return j
                                for key in ("prompt", "workflow"):
                                    if isinstance(j.get(key), dict) and (
                                        "nodes" in j[key]
                                        or any(
                                            isinstance(val, dict)
                                            and "class_type" in val
                                            for val in j[key].values()
                                        )
                                    ):
                                        return dict(j[key])
                    except Exception:
                        continue
        return None

    def _try_json_string(self, s: str):
        try:
            return json.loads(s)
        except Exception:
            return s

    # ---- Video probing (MP4/WEBM/MOV/MKV) ----
    def _probe_video_metadata(self, media_path: Path) -> Dict[str, Any]:
        """Best-effort probe to derive fps and frame count from a video.
        Tries ffprobe (if available), then OpenCV, then PyAV, then imageio. Returns empty dict on failure.
        """
        out: Dict[str, Any] = {}
        try:
            # 1) ffprobe (fast, accurate if installed)
            try:
                cmd = [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=r_frame_rate,avg_frame_rate,nb_frames,duration",
                    "-of",
                    "json",
                    str(media_path),
                ]
                data = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5)
                j = json.loads(data.decode("utf-8", errors="ignore"))
                streams = j.get("streams") or []
                if streams:
                    s = streams[0]
                    fps = self._parse_rate(s.get("avg_frame_rate")) or self._parse_rate(
                        s.get("r_frame_rate")
                    )
                    if fps:
                        out["fps"] = fps
                    if s.get("nb_frames") is not None:
                        try:
                            out["frames"] = (
                                int(s["nb_frames"])
                                if str(s["nb_frames"]).isdigit()
                                else None
                            )
                        except Exception:
                            pass
                    if "duration" in s and s["duration"]:
                        try:
                            out["duration"] = float(s["duration"])
                        except Exception:
                            pass
                    if out:
                        return out
            except Exception:
                pass
            # 2) OpenCV
            try:
                import cv2  # type: ignore

                cap = cv2.VideoCapture(str(media_path))
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 0
                    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    if fps and fps > 0:
                        out["fps"] = float(fps)
                    if frames and frames > 0:
                        out["frames"] = int(frames)
                    cap.release()
                    if out:
                        return out
            except Exception:
                pass
            # 3) PyAV
            try:
                import av  # type: ignore

                with av.open(str(media_path)) as container:
                    vstreams = [s for s in container.streams if s.type == "video"]
                    if vstreams:
                        vs = vstreams[0]
                        avg_rate = getattr(vs, "average_rate", None)
                        if avg_rate is not None:
                            try:
                                out["fps"] = float(avg_rate)
                            except Exception:
                                pass
                        if getattr(vs, "frames", None):
                            try:
                                if int(vs.frames) > 0:
                                    out["frames"] = int(vs.frames)
                            except Exception:
                                pass
                        duration = getattr(vs, "duration", None)
                        time_base = getattr(vs, "time_base", None)
                        if duration is not None and time_base is not None:
                            try:
                                out["duration"] = float(duration * time_base)
                            except Exception:
                                pass
                        if out:
                            return out
            except Exception:
                pass
            # 4) imageio (last resort)
            try:
                import imageio.v2 as iio  # type: ignore

                rdr = iio.get_reader(str(media_path))
                meta = rdr.get_meta_data()
                fps = meta.get("fps") or meta.get("framerate")
                if fps:
                    out["fps"] = float(fps)
                nframes = (
                    meta.get("nframes")
                    or (
                        meta.get("duration")
                        and out.get("fps")
                        and int(meta["duration"] * out["fps"])
                    )
                    or None
                )
                if nframes:
                    out["frames"] = int(nframes)
                rdr.close()
                return out
            except Exception:
                pass
        except Exception:
            return {}
        return out

    def _parse_rate(self, rate: Optional[str]) -> Optional[float]:
        if not rate:
            return None
        try:
            if "/" in rate:
                num_str, den_str = rate.split("/")
                num = float(num_str)
                den = float(den_str)
                return num / den if den else None
            return float(rate)
        except Exception:
            return None
