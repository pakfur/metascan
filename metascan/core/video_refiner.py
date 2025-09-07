"""
Video and image refiner module for metascan.
Handles upscaling using Real-ESRGAN for both images and videos.
"""

import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Tuple
import json
import urllib.request
from tqdm import tqdm
import logging

# Fix for Python 3.12+ distutils compatibility
try:
    import distutils  # type: ignore
except ImportError:
    # distutils was removed in Python 3.12, but setuptools provides compatibility
    try:
        import setuptools._distutils  # type: ignore

        sys.modules["distutils"] = setuptools._distutils
        # Also setup specific submodules that are commonly used
        for name in ["util", "version", "spawn", "log"]:
            try:
                module = getattr(setuptools._distutils, name, None)
                if module:
                    sys.modules[f"distutils.{name}"] = module
            except AttributeError:
                pass
    except ImportError:
        # Fallback - install setuptools
        import subprocess

        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "setuptools>=68.0.0"]
            )
            import setuptools._distutils  # type: ignore

            sys.modules["distutils"] = setuptools._distutils
        except Exception as e:
            print(f"Warning: Could not setup distutils compatibility: {e}")

# Fix for newer torchvision versions
if "torchvision.transforms.functional_tensor" not in sys.modules:
    try:
        import torchvision.transforms.functional as F

        sys.modules["torchvision.transforms.functional_tensor"] = F
    except ImportError:
        pass


class VideoRefiner:
    """Handles video and image upscaling for metascan."""

    def __init__(
        self,
        models_dir: Path,
        device: str = "auto",
        tile_size: int = 512,
        debug: bool = False,
    ):
        """
        Initialize the video refiner.

        Args:
            models_dir: Directory to store AI models
            device: Processing device (auto, cpu, mps)
            tile_size: Tile size for Real-ESRGAN processing
            debug: Enable debug logging
        """
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.tile_size = tile_size
        self.debug = debug

        # Setup logging
        log_level = logging.DEBUG if debug else logging.INFO
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        # Model URLs
        self.model_urls = {
            "RealESRGAN_x2plus.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
            "RealESRGAN_x4plus.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        }

        # Track if models are available
        self.models_available = False
        self._check_models()

    def _check_models(self) -> bool:
        """Check if required models are available."""
        required_models = ["RealESRGAN_x2plus.pth", "RealESRGAN_x4plus.pth"]
        self.models_available = all(
            (self.models_dir / model).exists() for model in required_models
        )
        return self.models_available

    def setup_models(
        self, progress_callback: Optional[Callable[[str, float], None]] = None
    ) -> bool:
        """
        Download and setup required models.

        Args:
            progress_callback: Callback for progress updates (message, progress_percent)

        Returns:
            True if setup successful, False otherwise
        """
        try:
            # Install Python dependencies
            if progress_callback:
                progress_callback("Installing Python dependencies...", 0)

            if not self._install_python_dependencies():
                return False

            # Download models
            if progress_callback:
                progress_callback("Downloading AI models...", 20)

            if not self._download_models(progress_callback):
                return False

            if progress_callback:
                progress_callback("Setup complete!", 100)

            self.models_available = True
            return True

        except Exception as e:
            self.logger.error(f"Setup failed: {e}")
            return False

    def _install_python_dependencies(self) -> bool:
        """Install required Python packages."""
        try:
            import realesrgan
            import basicsr
            import cv2

            self.logger.info("Dependencies already installed")
            return True
        except ImportError:
            self.logger.info("Installing dependencies...")
            try:
                packages = ["realesrgan", "basicsr", "opencv-python"]
                for package in packages:
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", package],
                        capture_output=True,
                        text=True,
                    )

                    if result.returncode != 0:
                        self.logger.error(
                            f"Failed to install {package}: {result.stderr}"
                        )
                        return False

                self.logger.info("Dependencies installed successfully")
                return True

            except Exception as e:
                self.logger.error(f"Failed to install dependencies: {e}")
                return False

    def _download_models(
        self, progress_callback: Optional[Callable[[str, float], None]] = None
    ) -> bool:
        """Download Real-ESRGAN models."""
        total_models = len(self.model_urls)

        for idx, (model_name, model_url) in enumerate(self.model_urls.items()):
            model_path = self.models_dir / model_name

            if model_path.exists():
                self.logger.info(f"Model already exists: {model_name}")
                continue

            self.logger.info(f"Downloading {model_name}...")

            if progress_callback:
                progress = 20 + (idx * 40 / total_models)
                progress_callback(f"Downloading {model_name}...", progress)

            if not self._download_file(model_url, model_path, model_name):
                return False

            if progress_callback:
                progress = 20 + ((idx + 1) * 40 / total_models)
                progress_callback(f"Downloaded {model_name}", progress)

        return True

    def _download_file(self, url: str, dest: Path, desc: str) -> bool:
        """Download a file with progress."""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)

            with urllib.request.urlopen(url) as response:
                total_size = int(response.headers.get("Content-Length", 0))

            with tqdm(total=total_size, unit="B", unit_scale=True, desc=desc) as pbar:

                def hook(block_num: int, block_size: int, total_size: int) -> None:
                    downloaded = block_num * block_size
                    pbar.update(downloaded - pbar.n)

                urllib.request.urlretrieve(url, dest, reporthook=hook)

            return True
        except Exception as e:
            self.logger.error(f"Failed to download {desc}: {e}")
            return False

    def process_image(
        self,
        input_path: Path,
        output_path: Path,
        scale: int = 2,
        replace_original: bool = False,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """
        Upscale a single image.

        Args:
            input_path: Path to input image
            output_path: Path to save upscaled image
            scale: Upscaling factor (2 or 4)
            replace_original: If True, replace original file
            progress_callback: Callback for progress updates (0-100)

        Returns:
            True if successful, False otherwise
        """
        try:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
            import cv2

            if progress_callback:
                progress_callback(10)

            # Select model based on scale
            if scale == 2:
                model = RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=2,
                )
                model_path = self.models_dir / "RealESRGAN_x2plus.pth"
            else:
                model = RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=4,
                )
                model_path = self.models_dir / "RealESRGAN_x4plus.pth"

            if not model_path.exists():
                self.logger.error(f"Model not found: {model_path}")
                return False

            if progress_callback:
                progress_callback(20)

            # Initialize upsampler
            upsampler = RealESRGANer(
                scale=scale,
                model_path=str(model_path),
                model=model,
                tile=self.tile_size,
                tile_pad=10,
                pre_pad=0,
                half=False,
                device=self.device if self.device != "auto" else None,
            )

            if progress_callback:
                progress_callback(30)

            # Read image
            img = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
            if img is None:
                self.logger.error(f"Failed to read image: {input_path}")
                return False

            if progress_callback:
                progress_callback(40)

            # Upscale
            output, _ = upsampler.enhance(img, outscale=scale)

            if progress_callback:
                progress_callback(80)

            # Handle output
            if replace_original:
                # Save to temp file first
                temp_path = output_path.with_suffix(".tmp" + output_path.suffix)
                cv2.imwrite(str(temp_path), output)

                # Move original to trash (or backup)
                backup_path = input_path.with_name(
                    input_path.stem + "_original" + input_path.suffix
                )
                shutil.move(str(input_path), str(backup_path))

                # Move temp to original location
                shutil.move(str(temp_path), str(input_path))
            else:
                # Save with suffix
                cv2.imwrite(str(output_path), output)

            if progress_callback:
                progress_callback(100)

            return True

        except ImportError as e:
            self.logger.error(f"Missing required libraries for image processing: {e}")
            self.logger.error(
                "Please install: pip install realesrgan basicsr opencv-python setuptools"
            )
            return False
        except Exception as e:
            self.logger.error(f"Failed to process image {input_path.name}: {e}")
            import traceback

            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False

    def process_video(
        self,
        input_path: Path,
        output_path: Path,
        scale: int = 2,
        fps: Optional[float] = None,
        replace_original: bool = False,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """
        Upscale a video.

        Args:
            input_path: Path to input video
            output_path: Path to save upscaled video
            scale: Upscaling factor (2 or 4)
            fps: Override FPS (None to keep original)
            replace_original: If True, replace original file
            progress_callback: Callback for progress updates (0-100)

        Returns:
            True if successful, False otherwise
        """
        temp_dir = None
        try:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
            import cv2

            # Create temp directory
            temp_dir = tempfile.mkdtemp(prefix="metascan_upscale_")
            temp_path = Path(temp_dir)

            if progress_callback:
                progress_callback(5)

            # Extract frames
            frames_dir = temp_path / "frames"
            frames_dir.mkdir()

            if not self._extract_frames(input_path, frames_dir, fps):
                return False

            if progress_callback:
                progress_callback(20)

            # Get video info
            video_info = self._get_video_info(input_path)

            # Count frames
            input_frames = sorted(list(frames_dir.glob("*.png")))
            if not input_frames:
                self.logger.error("No frames extracted")
                return False

            # Setup model
            if scale == 2:
                model = RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=2,
                )
                model_path = self.models_dir / "RealESRGAN_x2plus.pth"
            else:
                model = RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=4,
                )
                model_path = self.models_dir / "RealESRGAN_x4plus.pth"

            if not model_path.exists():
                self.logger.error(f"Model not found: {model_path}")
                return False

            # Initialize upsampler
            upsampler = RealESRGANer(
                scale=scale,
                model_path=str(model_path),
                model=model,
                tile=self.tile_size,
                tile_pad=10,
                pre_pad=0,
                half=False,
                device=self.device if self.device != "auto" else None,
            )

            # Upscale frames
            upscaled_dir = temp_path / "upscaled"
            upscaled_dir.mkdir()

            total_frames = len(input_frames)
            for idx, frame_path in enumerate(input_frames):
                if progress_callback:
                    progress = 20 + (idx * 60 / total_frames)
                    progress_callback(progress)

                img = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
                if img is None:
                    self.logger.error(f"Failed to read frame: {frame_path}")
                    continue

                output, _ = upsampler.enhance(img, outscale=scale)
                output_frame_path = upscaled_dir / frame_path.name
                cv2.imwrite(str(output_frame_path), output)

            if progress_callback:
                progress_callback(85)

            # Compile video
            if not self._compile_video(upscaled_dir, output_path, video_info):
                return False

            if progress_callback:
                progress_callback(95)

            # Handle output
            if replace_original:
                # Move original to backup
                backup_path = input_path.with_name(
                    input_path.stem + "_original" + input_path.suffix
                )
                shutil.move(str(input_path), str(backup_path))

                # Move upscaled to original location
                shutil.move(str(output_path), str(input_path))

            if progress_callback:
                progress_callback(100)

            return True

        except Exception as e:
            self.logger.error(f"Failed to process video: {e}")
            return False
        finally:
            # Cleanup temp directory
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _extract_frames(
        self, video_path: Path, output_dir: Path, fps: Optional[float] = None
    ) -> bool:
        """Extract frames from video."""
        try:
            cmd = [
                "ffmpeg",
                "-i",
                str(video_path),
                "-qscale:v",
                "1",
                "-qmin",
                "1",
                "-qmax",
                "1",
                "-vsync",
                "0",
            ]

            if fps:
                cmd.extend(["-r", str(fps)])

            cmd.append(str(output_dir / "frame_%08d.png"))

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                self.logger.error(f"Failed to extract frames: {result.stderr}")
                return False

            return True
        except Exception as e:
            self.logger.error(f"Error extracting frames: {e}")
            return False

    def _get_video_info(self, video_path: Path) -> dict:
        """Get video information."""
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate,codec_name",
                "-of",
                "json",
                str(video_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            info = json.loads(result.stdout)
            stream = info["streams"][0]

            fps_parts = stream["r_frame_rate"].split("/")
            fps = float(fps_parts[0]) / float(fps_parts[1])

            return {
                "width": int(stream["width"]),
                "height": int(stream["height"]),
                "fps": fps,
                "codec": stream["codec_name"],
            }
        except Exception as e:
            self.logger.error(f"Failed to get video info: {e}")
            return {"width": 1920, "height": 1080, "fps": 30.0, "codec": "h264"}

    def _compile_video(
        self, frames_dir: Path, output_path: Path, video_info: dict
    ) -> bool:
        """Compile frames back into video."""
        try:
            # Find frame pattern
            frames = list(frames_dir.glob("*.png"))
            if not frames:
                self.logger.error("No frames to compile")
                return False

            cmd = [
                "ffmpeg",
                "-y",
                "-r",
                str(video_info["fps"]),
                "-i",
                str(frames_dir / "frame_%08d.png"),
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "slow",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                self.logger.error(f"Failed to compile video: {result.stderr}")
                return False

            return True
        except Exception as e:
            self.logger.error(f"Error compiling video: {e}")
            return False

    def get_media_dimensions(self, file_path: Path) -> Tuple[int, int]:
        """
        Get the dimensions of an image or video file.

        Args:
            file_path: Path to the media file

        Returns:
            Tuple of (width, height), or (0, 0) if unable to determine
        """
        try:
            file_ext = file_path.suffix.lower()

            if file_ext in [
                ".mp4",
                ".avi",
                ".mov",
                ".mkv",
                ".webm",
                ".flv",
                ".wmv",
                ".m4v",
            ]:
                # Video file - use ffprobe
                video_info = self._get_video_info(file_path)
                return (video_info.get("width", 0), video_info.get("height", 0))
            else:
                # Image file - use PIL or cv2
                try:
                    from PIL import Image

                    with Image.open(file_path) as img:
                        return img.size  # PIL returns (width, height)
                except ImportError:
                    # Fallback to cv2
                    import cv2
                    import numpy as np

                    cv_img = cv2.imread(str(file_path))
                    if cv_img is not None:
                        height, width = cv_img.shape[:2]
                        return (width, height)

        except Exception as e:
            self.logger.error(f"Failed to get dimensions for {file_path}: {e}")

        return (0, 0)
