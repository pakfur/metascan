"""Embedding manager for image/video similarity features.

Manages CLIP model loading, perceptual hashing, and FAISS vector indexing
for duplicate detection, content search, and similarity search.
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# CLIP model registry - users select based on VRAM availability
CLIP_MODELS: Dict[str, Dict[str, Any]] = {
    "small": {
        "name": "ViT-B-16",
        "pretrained": "openai",
        "embedding_dim": 512,
        "vram_mb": 600,
        "description": "ViT-B/16 (fastest, ~600MB VRAM)",
    },
    "medium": {
        "name": "ViT-L-14",
        "pretrained": "openai",
        "embedding_dim": 768,
        "vram_mb": 1800,
        "description": "ViT-L/14 (balanced, ~1.8GB VRAM)",
    },
    "large": {
        "name": "ViT-H-14",
        "pretrained": "laion2b_s32b_b79k",
        "embedding_dim": 1024,
        "vram_mb": 4000,
        "description": "ViT-H/14 (best quality, ~4GB VRAM)",
    },
}

# Heavy imports loaded lazily
_heavy_imports_done = False
_open_clip = None
_torch = None
_imagehash = None
_faiss = None


def _ensure_heavy_imports() -> None:
    """Lazily import heavy dependencies (open_clip, torch, faiss, imagehash)."""
    global _heavy_imports_done, _open_clip, _torch, _imagehash, _faiss
    if _heavy_imports_done:
        return

    import open_clip
    import torch
    import imagehash
    import faiss

    _open_clip = open_clip
    _torch = torch
    _imagehash = imagehash
    _faiss = faiss
    _heavy_imports_done = True


class EmbeddingManager:
    """Manages CLIP model loading and embedding computation."""

    def __init__(
        self,
        model_key: str = "small",
        device: str = "auto",
    ) -> None:
        self.model_key = model_key
        self.device_preference = device
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._device: Optional[str] = None

    @property
    def model_config(self) -> Dict[str, Any]:
        """Get the configuration for the current model."""
        return CLIP_MODELS[self.model_key]

    @property
    def embedding_dim(self) -> int:
        """Get the embedding dimension for the current model."""
        return int(self.model_config["embedding_dim"])

    def _resolve_device(self) -> str:
        """Resolve the device to use for computation."""
        _ensure_heavy_imports()
        assert _torch is not None

        cuda_available = _torch.cuda.is_available()
        logger.info(
            f"Device selection: preference={self.device_preference}, "
            f"torch.cuda.is_available()={cuda_available}, "
            f"torch={_torch.__version__}, "
            f"cuda_built={_torch.version.cuda or 'none'}"
        )

        if self.device_preference == "auto":
            device = "cuda" if cuda_available else "cpu"
        else:
            device = self.device_preference

        if device == "cpu" and self.model_key in ("medium", "large"):
            logger.warning(
                f"Running {self.model_config['name']} on CPU will be very slow. "
                f"Consider using the 'small' model or a CUDA GPU. "
                f"If you have a GPU, ensure PyTorch is installed with CUDA support: "
                f"pip install torch --index-url https://download.pytorch.org/whl/cu124"
            )
        return device

    def _ensure_model_loaded(self) -> None:
        """Load the CLIP model if not already loaded."""
        if self._model is not None:
            return

        _ensure_heavy_imports()
        assert _open_clip is not None and _torch is not None

        self._device = self._resolve_device()
        config = self.model_config

        logger.info(
            f"Loading CLIP model {config['name']} "
            f"(pretrained={config['pretrained']}) on {self._device}"
        )

        # Check if model weights need to be downloaded
        needs_download = self._check_model_needs_download(
            config["name"], config["pretrained"]
        )
        if needs_download:
            logger.info(
                f"Model weights not found locally — downloading "
                f"{config['name']} ({config['pretrained']}). "
                f"This may take several minutes depending on your connection."
            )

        # Enable huggingface_hub download progress logging
        self._enable_download_logging()

        self._model, _, self._preprocess = _open_clip.create_model_and_transforms(
            config["name"],
            pretrained=config["pretrained"],
            device=self._device,
        )
        self._tokenizer = _open_clip.get_tokenizer(config["name"])
        assert self._model is not None
        self._model.eval()

        if needs_download:
            logger.info(f"Model download complete")
        logger.info(f"CLIP model loaded successfully on {self._device}")

    @staticmethod
    def _check_model_needs_download(model_name: str, pretrained: str) -> bool:
        """Check if model weights are already cached locally."""
        try:
            from huggingface_hub import try_to_load_from_cache

            # open_clip stores pretrained configs that map to HF repos.
            # We check the HF cache for common repo patterns.
            # If the check fails, assume download is needed.
            return False  # Conservative: only log if we positively detect a download
        except ImportError:
            pass

        try:
            # Fallback: check open_clip's local cache directory
            import open_clip

            # open_clip uses ~/.cache/clip or torch hub cache
            pretrained_cfg = open_clip.get_pretrained_cfg(model_name, pretrained)
            if pretrained_cfg and "url" in pretrained_cfg:
                import torch

                cache_dir = torch.hub.get_dir()
                # Check if any file matching the model exists in cache
                from pathlib import Path

                hub_cache = Path(cache_dir) / "checkpoints"
                if hub_cache.exists():
                    url = pretrained_cfg["url"]
                    filename = url.split("/")[-1]
                    if (hub_cache / filename).exists():
                        return False
                return True
            # HF-hosted model — check huggingface cache
            if pretrained_cfg and "hf_hub" in pretrained_cfg:
                from huggingface_hub import try_to_load_from_cache
                from huggingface_hub.utils import EntryNotFoundError

                hf_hub = pretrained_cfg["hf_hub"]
                repo_id = hf_hub.split("@")[0] if "@" in hf_hub else hf_hub
                result = try_to_load_from_cache(
                    repo_id, "open_clip_pytorch_model.safetensors"
                )
                return result is None
        except Exception as e:
            logger.debug(f"Could not check model cache: {e}")

        return False

    @staticmethod
    def _enable_download_logging() -> None:
        """Enable verbose logging for model weight downloads."""
        try:
            # Ensure huggingface_hub download progress is visible
            hf_logger = logging.getLogger("huggingface_hub.file_download")
            if not hf_logger.handlers:
                hf_logger.setLevel(logging.INFO)
                hf_logger.addHandler(logging.StreamHandler())

            # Also enable open_clip's own download logging
            oc_logger = logging.getLogger("open_clip")
            if not oc_logger.handlers:
                oc_logger.setLevel(logging.INFO)
                oc_logger.addHandler(logging.StreamHandler())
        except Exception:
            pass

    def unload_model(self) -> None:
        """Free GPU/CPU memory by unloading the model."""
        if self._model is not None:
            _ensure_heavy_imports()
            assert _torch is not None

            del self._model
            del self._preprocess
            del self._tokenizer
            self._model = None
            self._preprocess = None
            self._tokenizer = None

            if self._device == "cuda":
                _torch.cuda.empty_cache()

            self._device = None
            logger.info("CLIP model unloaded")

    @staticmethod
    def _load_and_downsize(image_path: str, max_size: int = 512) -> Image.Image:
        """Load an image and downsize if larger than max_size.

        CLIP preprocesses to 224x224 anyway, so there's no quality benefit
        to loading a 4096x4096 image. Downsizing first avoids excessive
        RAM usage and speeds up preprocessing significantly on CPU.
        """
        image = Image.open(image_path).convert("RGB")
        w, h = image.size
        if max(w, h) > max_size:
            scale = max_size / max(w, h)
            image = image.resize(
                (int(w * scale), int(h * scale)), Image.Resampling.LANCZOS
            )
        return image

    def compute_image_embedding(self, image_path: str) -> Optional[np.ndarray]:
        """Compute a CLIP embedding for an image file.

        Returns an L2-normalized embedding vector, or None on error.
        """
        try:
            self._ensure_model_loaded()
            assert (
                self._model is not None
                and self._preprocess is not None
                and _torch is not None
            )

            image = self._load_and_downsize(image_path)
            image_tensor = self._preprocess(image).unsqueeze(0).to(self._device)

            with _torch.no_grad(), _torch.amp.autocast(
                device_type=self._device if self._device != "cpu" else "cpu",
                enabled=self._device != "cpu",
            ):
                embedding = self._model.encode_image(image_tensor)

            embedding = embedding.cpu().numpy().astype(np.float32)
            # L2 normalize
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding.flatten()

        except Exception as e:
            logger.error(f"Failed to compute image embedding for {image_path}: {e}")
            return None

    def compute_text_embedding(self, text: str) -> Optional[np.ndarray]:
        """Compute a CLIP text embedding for a search query.

        Returns an L2-normalized embedding vector, or None on error.
        """
        try:
            self._ensure_model_loaded()
            assert (
                self._model is not None
                and self._tokenizer is not None
                and _torch is not None
            )

            tokens = self._tokenizer([text]).to(self._device)

            with _torch.no_grad(), _torch.amp.autocast(
                device_type=self._device if self._device != "cpu" else "cpu",
                enabled=self._device != "cpu",
            ):
                embedding = self._model.encode_text(tokens)

            embedding = embedding.cpu().numpy().astype(np.float32)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding.flatten()

        except Exception as e:
            logger.error(f"Failed to compute text embedding: {e}")
            return None

    def compute_video_embedding(
        self, video_path: str, num_keyframes: int = 4
    ) -> Optional[np.ndarray]:
        """Compute a CLIP embedding for a video by averaging keyframe embeddings.

        Extracts evenly-spaced keyframes using ffmpeg, computes CLIP embeddings
        for each, and returns the L2-normalized average.
        """
        try:
            frames = self._extract_keyframes(video_path, num_keyframes)
            if not frames:
                logger.warning(f"No keyframes extracted from {video_path}")
                return None

            self._ensure_model_loaded()
            assert (
                self._model is not None
                and self._preprocess is not None
                and _torch is not None
            )

            embeddings = []
            for frame in frames:
                image = Image.fromarray(frame).convert("RGB")
                image_tensor = self._preprocess(image).unsqueeze(0).to(self._device)

                with _torch.no_grad(), _torch.amp.autocast(
                    device_type=self._device if self._device != "cpu" else "cpu",
                    enabled=self._device != "cpu",
                ):
                    emb = self._model.encode_image(image_tensor)
                embeddings.append(emb.cpu().numpy().astype(np.float32))

            # Average and normalize
            avg_embedding = np.mean(embeddings, axis=0)
            norm = np.linalg.norm(avg_embedding)
            if norm > 0:
                avg_embedding = avg_embedding / norm
            return avg_embedding.flatten()

        except Exception as e:
            logger.error(f"Failed to compute video embedding for {video_path}: {e}")
            return None

    def _extract_keyframes(self, video_path: str, num_frames: int) -> List[np.ndarray]:
        """Extract evenly-spaced keyframes from a video using ffmpeg."""
        from metascan.utils.ffmpeg_utils import (
            probe_with_timeout,
            extract_frame_with_timeout,
        )

        frames: List[np.ndarray] = []
        try:
            probe = probe_with_timeout(video_path)
            if not probe:
                logger.warning(f"ffprobe failed for {video_path}")
                return frames

            video_stream = next(
                (s for s in probe["streams"] if s["codec_type"] == "video"), None
            )
            if not video_stream:
                logger.warning(f"No video stream found in {video_path}")
                return frames

            duration = float(video_stream.get("duration", 0))
            if duration <= 0:
                # Try container-level duration
                duration = float(probe.get("format", {}).get("duration", 0))
            if duration <= 0:
                # Fallback: extract just the first frame
                num_frames = 1
                timestamps = [0.0]
            else:
                timestamps = [
                    duration * i / (num_frames + 1) for i in range(1, num_frames + 1)
                ]

            width = int(video_stream.get("width", 224))
            height = int(video_stream.get("height", 224))

            for ts in timestamps:
                out = extract_frame_with_timeout(video_path, ts, width, height)
                if out:
                    try:
                        frame = np.frombuffer(out, np.uint8).reshape(height, width, 3)
                        frames.append(frame)
                    except ValueError as e:
                        logger.debug(f"Frame reshape failed at {ts}s: {e}")
                else:
                    logger.debug(
                        f"Frame extraction returned no data at {ts:.1f}s "
                        f"for {video_path}"
                    )

        except Exception as e:
            logger.error(f"Failed to extract keyframes from {video_path}: {e}")

        return frames

    @staticmethod
    def compute_phash(image_path: str) -> Optional[str]:
        """Compute a perceptual hash for an image file.

        Returns the hex string representation of the pHash, or None on error.
        """
        try:
            _ensure_heavy_imports()
            assert _imagehash is not None

            image = Image.open(image_path).convert("RGB")
            phash = _imagehash.phash(image)
            return str(phash)
        except Exception as e:
            logger.error(f"Failed to compute pHash for {image_path}: {e}")
            return None

    @staticmethod
    def compute_video_phash(video_path: str) -> Optional[str]:
        """Compute a perceptual hash for a video using its first frame."""
        from metascan.utils.ffmpeg_utils import (
            probe_with_timeout,
            extract_frame_with_timeout,
        )

        try:
            probe = probe_with_timeout(video_path)
            if not probe:
                return None

            video_stream = next(
                (s for s in probe["streams"] if s["codec_type"] == "video"), None
            )
            if not video_stream:
                return None

            width = int(video_stream.get("width", 224))
            height = int(video_stream.get("height", 224))

            out = extract_frame_with_timeout(video_path, 0, width, height)
            if not out:
                return None

            _ensure_heavy_imports()
            assert _imagehash is not None

            frame = np.frombuffer(out, np.uint8).reshape(height, width, 3)
            image = Image.fromarray(frame)
            phash = _imagehash.phash(image)
            return str(phash)

        except Exception as e:
            logger.error(f"Failed to compute video pHash for {video_path}: {e}")
            return None

    @staticmethod
    def compute_phash_distance(hash1: str, hash2: str) -> int:
        """Compute the hamming distance between two pHash hex strings."""
        _ensure_heavy_imports()
        assert _imagehash is not None
        h1 = _imagehash.hex_to_hash(hash1)
        h2 = _imagehash.hex_to_hash(hash2)
        return int(h1 - h2)


class FaissIndexManager:
    """Manages a FAISS index for vector similarity search."""

    def __init__(self, index_dir: Path) -> None:
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)

        self._index_path = self.index_dir / "faiss_index.bin"
        self._mapping_path = self.index_dir / "id_mapping.json"
        self._meta_path = self.index_dir / "index_meta.json"

        self._index = None
        self._id_to_path: List[str] = []
        self._path_to_id: Dict[str, int] = {}
        self._meta: Dict[str, Any] = {}
        self._dirty = False

    @property
    def is_loaded(self) -> bool:
        """Whether the index is currently loaded in memory."""
        return self._index is not None

    @property
    def file_count(self) -> int:
        """Number of vectors in the index."""
        return len(self._id_to_path)

    @property
    def meta(self) -> Dict[str, Any]:
        """Index metadata (model, dimension, count)."""
        return self._meta

    def load(self) -> bool:
        """Load the FAISS index and ID mapping from disk."""
        try:
            _ensure_heavy_imports()
            assert _faiss is not None

            if self._index_path.exists() and self._mapping_path.exists():
                self._index = _faiss.read_index(str(self._index_path))

                with open(self._mapping_path, "r") as f:
                    self._id_to_path = json.load(f)

                self._path_to_id = {
                    path: idx for idx, path in enumerate(self._id_to_path)
                }

                if self._meta_path.exists():
                    with open(self._meta_path, "r") as f:
                        self._meta = json.load(f)

                assert self._index is not None
                logger.info(f"Loaded FAISS index with {self._index.ntotal} vectors")
                return True
            else:
                logger.info("No existing FAISS index found")
                return False

        except Exception as e:
            logger.error(f"Failed to load FAISS index: {e}")
            self._index = None
            self._id_to_path = []
            self._path_to_id = {}
            return False

    def create(self, embedding_dim: int, model_key: str) -> None:
        """Create a new empty FAISS index."""
        _ensure_heavy_imports()
        assert _faiss is not None

        self._index = _faiss.IndexFlatIP(embedding_dim)
        self._id_to_path = []
        self._path_to_id = {}
        self._meta = {
            "model_key": model_key,
            "embedding_dim": embedding_dim,
            "file_count": 0,
        }
        self._dirty = True
        logger.info(f"Created new FAISS index (dim={embedding_dim}, model={model_key})")

    def add(self, file_path: str, embedding: np.ndarray) -> None:
        """Add a single embedding to the index."""
        if self._index is None:
            raise RuntimeError("Index not loaded or created")

        # If path already exists, skip (no duplicates in index)
        if file_path in self._path_to_id:
            return

        vector = embedding.reshape(1, -1).astype(np.float32)
        self._index.add(vector)
        idx = len(self._id_to_path)
        self._id_to_path.append(file_path)
        self._path_to_id[file_path] = idx
        self._dirty = True

    def add_batch(self, file_paths: List[str], embeddings: np.ndarray) -> None:
        """Add a batch of embeddings to the index."""
        if self._index is None:
            raise RuntimeError("Index not loaded or created")

        # Filter out paths already in index
        new_indices = []
        for i, path in enumerate(file_paths):
            if path not in self._path_to_id:
                new_indices.append(i)

        if not new_indices:
            return

        new_paths = [file_paths[i] for i in new_indices]
        new_vectors = embeddings[new_indices].astype(np.float32)

        self._index.add(new_vectors)
        for path in new_paths:
            idx = len(self._id_to_path)
            self._id_to_path.append(path)
            self._path_to_id[path] = idx

        self._dirty = True

    def search(
        self, query_vector: np.ndarray, top_k: int = 50
    ) -> List[Tuple[str, float]]:
        """Search for the top-K most similar vectors.

        Returns list of (file_path, similarity_score) tuples, sorted by score descending.
        """
        if self._index is None or self._index.ntotal == 0:
            return []

        k = min(top_k, self._index.ntotal)
        query = query_vector.reshape(1, -1).astype(np.float32)
        scores, indices = self._index.search(query, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0 and idx < len(self._id_to_path):
                results.append((self._id_to_path[idx], float(score)))

        return results

    def get_embedding(self, file_path: str) -> Optional[np.ndarray]:
        """Retrieve the stored embedding for a file path."""
        if self._index is None or file_path not in self._path_to_id:
            return None

        idx = self._path_to_id[file_path]
        vector = np.zeros(self._index.d, dtype=np.float32)
        self._index.reconstruct(idx, vector)
        return vector

    def has_file(self, file_path: str) -> bool:
        """Check if a file is already in the index."""
        return file_path in self._path_to_id

    def remove_stale_entries(self, valid_paths: set) -> int:
        """Remove entries for files that no longer exist.

        Since FAISS IndexFlatIP doesn't support removal, this rebuilds the index.
        Returns the number of removed entries.
        """
        if self._index is None:
            return 0

        stale = [p for p in self._id_to_path if p not in valid_paths]
        if not stale:
            return 0

        _ensure_heavy_imports()
        assert _faiss is not None

        # Rebuild index without stale entries
        dim = self._index.d
        new_index = _faiss.IndexFlatIP(dim)
        new_id_to_path = []
        new_path_to_id = {}

        for i, path in enumerate(self._id_to_path):
            if path in valid_paths:
                vector = np.zeros(dim, dtype=np.float32)
                self._index.reconstruct(i, vector)
                new_index.add(vector.reshape(1, -1))
                new_path_to_id[path] = len(new_id_to_path)
                new_id_to_path.append(path)

        removed = len(stale)
        self._index = new_index
        self._id_to_path = new_id_to_path
        self._path_to_id = new_path_to_id
        self._dirty = True

        logger.info(f"Removed {removed} stale entries from FAISS index")
        return removed

    def save(self) -> bool:
        """Save the FAISS index and ID mapping to disk."""
        if self._index is None:
            return False

        try:
            _ensure_heavy_imports()
            assert _faiss is not None

            # Write to temp files first, then atomically replace
            temp_index = self._index_path.with_suffix(".tmp")
            _faiss.write_index(self._index, str(temp_index))
            os.replace(str(temp_index), str(self._index_path))

            temp_mapping = self._mapping_path.with_suffix(".tmp")
            with open(temp_mapping, "w") as f:
                json.dump(self._id_to_path, f)
            os.replace(str(temp_mapping), str(self._mapping_path))

            self._meta["file_count"] = len(self._id_to_path)
            temp_meta = self._meta_path.with_suffix(".tmp")
            with open(temp_meta, "w") as f:
                json.dump(self._meta, f, indent=2)
            os.replace(str(temp_meta), str(self._meta_path))

            self._dirty = False
            logger.info(f"Saved FAISS index ({len(self._id_to_path)} vectors)")
            return True

        except Exception as e:
            logger.error(f"Failed to save FAISS index: {e}")
            return False

    def clear(self) -> None:
        """Clear the index and all associated files."""
        self._index = None
        self._id_to_path = []
        self._path_to_id = {}
        self._meta = {}
        self._dirty = False

        for path in [self._index_path, self._mapping_path, self._meta_path]:
            if path.exists():
                path.unlink()

        logger.info("FAISS index cleared")

    def check_model_match(self, model_key: str) -> bool:
        """Check if the index was built with the specified model."""
        return self._meta.get("model_key") == model_key
