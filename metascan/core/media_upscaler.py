"""
Media upscaler module for metascan.
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

try:
    import distutils  # type: ignore
except ImportError:
    try:
        import setuptools._distutils  # type: ignore

        sys.modules["distutils"] = setuptools._distutils
        for name in ["util", "version", "spawn", "log"]:
            try:
                module = getattr(setuptools._distutils, name, None)
                if module:
                    sys.modules[f"distutils.{name}"] = module
            except AttributeError:
                pass
    except ImportError:
        import subprocess

        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "setuptools>=68.0.0"]
            )
            import setuptools._distutils  # type: ignore

            sys.modules["distutils"] = setuptools._distutils
        except Exception as e:
            print(f"Warning: Could not setup distutils compatibility: {e}")

if "torchvision.transforms.functional_tensor" not in sys.modules:
    try:
        import torchvision.transforms.functional as F

        sys.modules["torchvision.transforms.functional_tensor"] = F
    except ImportError:
        pass


class MediaUpscaler:
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

        log_level = logging.DEBUG if debug else logging.INFO
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(log_level)
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

        self.model_urls = {
            "RealESRGAN_x2plus.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
            "RealESRGAN_x4plus.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
            "RealESRGAN_x4plus_anime_6B.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
            "GFPGANv1.4.pth": "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
            "rife_binary": "https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-macos.zip",
        }

        self.rife_bin: Optional[Path] = None

        self.models_available = False
        self._check_models()

    def _check_models(self) -> bool:
        required_models = [
            "RealESRGAN_x2plus.pth",
            "RealESRGAN_x4plus.pth",
            "RealESRGAN_x4plus_anime_6B.pth",
            "GFPGANv1.4.pth",
        ]
        self.models_available = all(
            (self.models_dir / model).exists() for model in required_models
        )

        rife_bin_path = (
            self.models_dir
            / "rife"
            / "rife-ncnn-vulkan-20221029-macos"
            / "rife-ncnn-vulkan"
        )
        if rife_bin_path.exists():
            self.rife_bin = rife_bin_path
            self.logger.debug(f"RIFE binary found at: {rife_bin_path}")
        else:
            self.logger.debug(f"RIFE binary not found at: {rife_bin_path}")
            self.logger.debug(f"RIFE directory exists: {rife_bin_path.parent.exists()}")
            if rife_bin_path.parent.exists():
                rife_files = list(rife_bin_path.parent.iterdir())
                self.logger.debug(f"Files in RIFE directory: {rife_files}")

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
            if progress_callback:
                progress_callback("Installing Python dependencies...", 0)

            if not self._install_python_dependencies():
                return False

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
        try:
            import realesrgan
            import basicsr
            import cv2
            import gfpgan
            from PIL import Image
            import piexif

            self.logger.info("Dependencies already installed")
            return True
        except ImportError:
            self.logger.info("Installing dependencies...")
            try:
                packages = [
                    "realesrgan",
                    "basicsr",
                    "opencv-python",
                    "gfpgan",
                    "Pillow",
                    "piexif",
                ]
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

        if progress_callback:
            progress_callback("Setting up RIFE for frame interpolation...", 70.0)

        if not self._download_rife():
            self.logger.warning(
                "Failed to download RIFE - frame interpolation will use basic blending"
            )

        if progress_callback:
            progress_callback("Setup complete!", 100.0)

        return True

    def _download_file(self, url: str, dest: Path, desc: str) -> bool:
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

    def _download_rife(self) -> bool:
        import zipfile
        import tarfile

        bin_dir = self.models_dir / "rife"
        # The actual binary is in the extracted subdirectory
        bin_path = bin_dir / "rife-ncnn-vulkan-20221029-macos" / "rife-ncnn-vulkan"

        if bin_path.exists():
            self.rife_bin = bin_path
            self.logger.info("RIFE already installed")
            return True

        self.logger.info("Downloading RIFE...")
        self.logger.debug(f"RIFE binary target path: {bin_path}")
        self.logger.debug(f"RIFE bin directory: {bin_dir}")

        try:
            # Download binary
            zip_path = self.models_dir / "rife.zip"
            if not self._download_file(
                self.model_urls["rife_binary"], zip_path, "RIFE binary"
            ):
                return False

            # Extract
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(bin_dir)

            # Make executable
            bin_path.chmod(0o755)
            self.rife_bin = bin_path

            # Clean up
            zip_path.unlink()

            # The RIFE binary package includes models, no need to download separately
            model_path = bin_dir / "rife-ncnn-vulkan-20221029-macos" / "rife-v4.6"
            if not model_path.exists():
                self.logger.info("RIFE models will be included in the binary package")
            else:
                self.logger.info("RIFE v4.6 model already available in binary package")

            self.logger.info("RIFE setup complete")
            return True

        except Exception as e:
            self.logger.error(f"Failed to download RIFE: {e}")
            return False

    def enhance_faces_gfpgan(
        self,
        input_path: Path,
        output_path: Path,
        bg_upsampler: Optional[str] = None,
        progress_callback: Optional[Callable[[float], bool]] = None,
    ) -> bool:
        """
        Enhance faces in an image using GFPGAN.

        Args:
            input_path: Path to input image
            output_path: Path to save enhanced image
            bg_upsampler: Background upsampler ('realesrgan' or None)
            progress_callback: Callback for progress updates (0-100)

        Returns:
            True if successful, False otherwise
        """
        import time

        start_time = time.time()

        try:
            from gfpgan import GFPGANer  # type: ignore
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
            import cv2

            if progress_callback:
                progress_callback(10)

            # Setup GFPGAN model
            gfpgan_model_path = self.models_dir / "GFPGANv1.4.pth"
            if not gfpgan_model_path.exists():
                self.logger.error(f"GFPGAN model not found: {gfpgan_model_path}")
                return False

            # Setup background upsampler if requested
            bg_upsampler_instance = None
            if bg_upsampler == "realesrgan":
                model = RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=2,
                )
                model_path = self.models_dir / "RealESRGAN_x2plus.pth"
                if model_path.exists():
                    bg_upsampler_instance = RealESRGANer(
                        scale=2,
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

            # Initialize GFPGAN
            restorer = GFPGANer(
                model_path=str(gfpgan_model_path),
                upscale=2,
                arch="clean",
                channel_multiplier=2,
                bg_upsampler=bg_upsampler_instance,
                device=self.device if self.device != "auto" else None,
            )

            if progress_callback:
                progress_callback(50)

            # Read image
            input_img = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
            if input_img is None:
                self.logger.error(f"Failed to read image: {input_path}")
                return False

            if progress_callback:
                progress_callback(60)

            # Enhance faces
            _, _, output = restorer.enhance(
                input_img, has_aligned=False, only_center_face=False, paste_back=True
            )

            if progress_callback:
                progress_callback(90)

            # Save result
            cv2.imwrite(str(output_path), output)

            if progress_callback:
                # Check if callback returns False (indicating cancellation)
                if not progress_callback(100):
                    self.logger.info("Processing cancelled by user")
                    return False

            # Log completion with elapsed time
            elapsed_time = time.time() - start_time
            self.logger.info(
                f"Completed frame interpolation: {input_path.name} in {elapsed_time:.1f}s"
            )
            return True

        except ImportError as e:
            self.logger.error(f"Missing required libraries for face enhancement: {e}")
            self.logger.error("Please install: pip install gfpgan")
            return False
        except Exception as e:
            self.logger.error(f"Failed to enhance faces in {input_path.name}: {e}")
            import traceback

            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False

    def process_image(
        self,
        input_path: Path,
        output_path: Path,
        scale: int = 2,
        enhance_faces: bool = False,
        model_type: str = "general",
        preserve_metadata: bool = True,
        progress_callback: Optional[Callable[[float], bool]] = None,
    ) -> bool:
        """
        Upscale a single image with optional face enhancement.
        Original file will be moved to trash after successful upscaling.

        Args:
            input_path: Path to input image
            output_path: Path to save upscaled image (temporary, will be moved to input_path)
            scale: Upscaling factor (2 or 4)
            enhance_faces: If True, enhance faces using GFPGAN
            model_type: Type of model to use ('general' or 'anime')
            preserve_metadata: If True, preserve original metadata
            progress_callback: Callback for progress updates (0-100)

        Returns:
            True if successful, False otherwise
        """
        import time

        start_time = time.time()

        # Log upscale operation details
        operation_details = []
        operation_details.append(f"{scale}x upscale")
        if enhance_faces:
            operation_details.append("face enhancement")
        operation_details.append(f"{model_type} model")

        operation_str = ", ".join(operation_details)
        self.logger.info(f"Starting image upscale: {input_path.name} - {operation_str}")

        try:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
            import cv2

            if progress_callback:
                progress_callback(10)

            # If face enhancement is requested, use GFPGAN with background upscaling
            if enhance_faces:
                from gfpgan import GFPGANer  # type: ignore

                # Setup background upsampler for GFPGAN
                model, model_path = self._get_upscale_model_info(scale, model_type)

                if not model_path.exists():
                    self.logger.error(f"Model not found: {model_path}")
                    return False

                bg_upsampler = RealESRGANer(
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

                # Setup GFPGAN
                gfpgan_model_path = self.models_dir / "GFPGANv1.4.pth"
                if not gfpgan_model_path.exists():
                    self.logger.error(f"GFPGAN model not found: {gfpgan_model_path}")
                    return False

                restorer = GFPGANer(
                    model_path=str(gfpgan_model_path),
                    upscale=scale,
                    arch="clean",
                    channel_multiplier=2,
                    bg_upsampler=bg_upsampler,
                    device=self.device if self.device != "auto" else None,
                )

                if progress_callback:
                    progress_callback(50)

                # Read image
                img = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
                if img is None:
                    self.logger.error(f"Failed to read image: {input_path}")
                    return False

                if progress_callback:
                    progress_callback(60)

                # Enhance with GFPGAN (includes background upscaling)
                _, _, output = restorer.enhance(
                    img, has_aligned=False, only_center_face=False, paste_back=True
                )

                if progress_callback:
                    progress_callback(80)
            else:
                # Standard upscaling without face enhancement
                # Select model based on scale and type
                model, model_path = self._get_upscale_model_info(scale, model_type)

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

            # Handle output - always replace original
            # Save to the provided output_path first (which has suffix)
            cv2.imwrite(str(output_path), output)

            # Save the target path (where original was) before moving to trash
            final_path = input_path

            # Move original to trash
            if self._move_to_trash(input_path):
                # Move upscaled from output_path to original location
                # Note: input_path no longer exists as a file, but we can use it as the destination
                self.logger.debug(
                    f"Moving upscaled file from {output_path} to {final_path}"
                )
                shutil.move(str(output_path), str(final_path))

                # Update output_path to reflect the final location
                output_path = final_path
                self.logger.info(f"Upscaled file now at: {final_path}")
            else:
                self.logger.error(
                    "Failed to move original to trash, keeping both files"
                )
                # Keep the upscaled file at output_path (with suffix)

            if progress_callback:
                # Check if callback returns False (indicating cancellation)
                if not progress_callback(100):
                    self.logger.info("Processing cancelled by user")
                    return False

            # Log completion with elapsed time
            elapsed_time = time.time() - start_time
            self.logger.info(
                f"Completed image upscale: {input_path.name} in {elapsed_time:.1f}s"
            )
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
        enhance_faces: bool = False,
        model_type: str = "general",
        preserve_metadata: bool = True,
        progress_callback: Optional[Callable[[float], bool]] = None,
    ) -> bool:
        """
        Upscale a video with optional face enhancement.
        Original file will be moved to trash after successful upscaling.

        Args:
            input_path: Path to input video
            output_path: Path to save upscaled video (temporary, will be moved to input_path)
            scale: Upscaling factor (2 or 4)
            fps: Override FPS (None to keep original)
            enhance_faces: If True, enhance faces using GFPGAN
            model_type: Type of model to use ('general' or 'anime')
            preserve_metadata: If True, preserve original metadata
            progress_callback: Callback for progress updates (0-100)

        Returns:
            True if successful, False otherwise
        """
        import time

        start_time = time.time()

        # Log upscale operation details
        operation_details = []
        operation_details.append(f"{scale}x upscale")
        if enhance_faces:
            operation_details.append("face enhancement")
        operation_details.append(f"{model_type} model")
        if fps is not None:
            operation_details.append(f"custom FPS: {fps}")

        operation_str = ", ".join(operation_details)
        self.logger.info(f"Starting video upscale: {input_path.name} - {operation_str}")

        temp_dir = None
        try:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
            import cv2

            # Create temp directory
            temp_dir = tempfile.mkdtemp(prefix="metascan_upscale_")
            temp_path = Path(temp_dir)
            self.logger.info(f"Using temporary directory: {str(temp_path)}")

            if progress_callback:
                if not progress_callback(5):
                    self.logger.info("Video processing cancelled by user")
                    return False

            # Extract frames
            frames_dir = temp_path / "frames"
            frames_dir.mkdir()

            if not self._extract_frames(input_path, frames_dir, fps):
                return False

            if progress_callback:
                if not progress_callback(20):
                    self.logger.info("Video processing cancelled by user")
                    return False

            # Get video info
            video_info = self._get_video_info(input_path)

            # Count frames
            input_frames = sorted(list(frames_dir.glob("*.png")))
            if not input_frames:
                self.logger.error("No frames extracted")
                return False

            # Setup model
            model, model_path = self._get_upscale_model_info(scale, model_type)

            if not model_path.exists():
                self.logger.error(f"Model not found: {model_path}")
                return False

            # Setup processing pipeline
            if enhance_faces:
                from gfpgan import GFPGANer  # type: ignore

                # Background upsampler for GFPGAN
                bg_upsampler = RealESRGANer(
                    scale=scale,
                    model_path=str(model_path),
                    model=model,
                    tile=self.tile_size,
                    tile_pad=10,
                    pre_pad=0,
                    half=False,
                    device=self.device if self.device != "auto" else None,
                )

                # GFPGAN model
                gfpgan_model_path = self.models_dir / "GFPGANv1.4.pth"
                if not gfpgan_model_path.exists():
                    self.logger.error(f"GFPGAN model not found: {gfpgan_model_path}")
                    return False

                restorer = GFPGANer(
                    model_path=str(gfpgan_model_path),
                    upscale=scale,
                    arch="clean",
                    channel_multiplier=2,
                    bg_upsampler=bg_upsampler,
                    device=self.device if self.device != "auto" else None,
                )
            else:
                # Standard upsampler
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

            # Process frames
            upscaled_dir = temp_path / "upscaled"
            upscaled_dir.mkdir()

            total_frames = len(input_frames)
            for idx, frame_path in enumerate(input_frames):
                self.logger.info(f"Upscaling frame {idx} of {total_frames}")

                if progress_callback:
                    progress = 20 + (idx * 60 / total_frames)
                    # Check if callback returns False (indicating cancellation)
                    if not progress_callback(progress):
                        self.logger.info("Video processing cancelled by user")
                        return False

                img = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
                if img is None:
                    self.logger.error(f"Failed to read frame: {frame_path}")
                    continue

                # Process frame based on mode
                if enhance_faces:
                    _, _, output = restorer.enhance(
                        img, has_aligned=False, only_center_face=False, paste_back=True
                    )
                else:
                    output, _ = upsampler.enhance(img, outscale=scale)

                output_frame_path = upscaled_dir / frame_path.name
                cv2.imwrite(str(output_frame_path), output)

            if progress_callback:
                if not progress_callback(85):
                    self.logger.info("Video processing cancelled by user")
                    return False

            # Override FPS if specified
            if fps is not None:
                video_info["fps"] = fps

            # Compile video
            self.logger.info(f"Compiling video to {str(output_path)}")

            if not self._compile_video(upscaled_dir, output_path, video_info):
                return False

            if progress_callback:
                if not progress_callback(95):
                    self.logger.info("Video processing cancelled by user")
                    return False

            # Handle output - always replace original
            # Save the target path (where original was) before moving to trash
            final_path = input_path

            # Move original to trash
            if self._move_to_trash(input_path):
                self.logger.info(f"Move '{str(input_path)}' to trash")
                # Move upscaled from output_path to original location
                # Note: input_path no longer exists as a file, but we can use it as the destination
                shutil.move(str(output_path), str(final_path))

                # Update output_path to reflect the final location
                output_path = final_path
                self.logger.info(f"Upscaled video now at: {final_path}")
            else:
                self.logger.error(
                    "Failed to move original to trash, keeping both files"
                )
                # Keep the upscaled file at output_path (with suffix)

            if progress_callback:
                # Check if callback returns False (indicating cancellation)
                if not progress_callback(100):
                    self.logger.info("Processing cancelled by user")
                    return False

            # Log completion with elapsed time
            elapsed_time = time.time() - start_time
            self.logger.info(
                f"Completed video upscale: {input_path.name} in {elapsed_time:.1f}s"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to process video: {e}")
            return False
        finally:
            # Cleanup temp directory
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def interpolate_frames_rife(
        self,
        input_path: Path,
        output_path: Path,
        interpolation_factor: int = 2,
        replace_original: bool = False,
        progress_callback: Optional[Callable[[float], bool]] = None,
    ) -> bool:
        """
        Interpolate video frames using RIFE for smoother motion.

        Args:
            input_path: Path to input video
            output_path: Path to save interpolated video
            interpolation_factor: Factor to multiply FPS by (2, 4, 8)
            replace_original: If True, replace original file
            progress_callback: Callback for progress updates (0-100)

        Returns:
            True if successful, False otherwise
        """
        import time

        start_time = time.time()

        self.logger.info(
            f"Starting frame interpolation: {input_path.name} - {interpolation_factor}x interpolation using RIFE"
        )

        # Check if RIFE binary is available, try to download if not
        # Re-scan for RIFE binary if not currently set (handles case where RIFE was downloaded by another instance)
        if self.rife_bin is None:
            rife_bin_path = (
                self.models_dir
                / "rife"
                / "rife-ncnn-vulkan-20221029-macos"
                / "rife-ncnn-vulkan"
            )
            if rife_bin_path.exists():
                self.rife_bin = rife_bin_path
                self.logger.debug(
                    f"RIFE binary found during re-scan at: {rife_bin_path}"
                )

        if self.rife_bin is None or not self.rife_bin.exists():
            self.logger.debug(
                f"RIFE binary status: rife_bin={self.rife_bin}, exists={self.rife_bin.exists() if self.rife_bin else False}"
            )
            # Try to download RIFE if it's not available
            if self.rife_bin is None:
                self.logger.info("RIFE binary not found, attempting to download...")
                if progress_callback:
                    progress_callback(10)  # Show some progress
                if self._download_rife():
                    self.logger.info("RIFE binary downloaded successfully")
                    # Re-check RIFE after download
                    if self.rife_bin and self.rife_bin.exists():
                        self.logger.info(
                            "RIFE binary now available, proceeding with RIFE interpolation"
                        )
                    else:
                        self.logger.warning(
                            "RIFE download succeeded but binary still not found"
                        )
                        self.logger.warning(
                            "RIFE binary not available. Interpolation not possible."
                        )
                        return False
                else:
                    self.logger.warning("Failed to download RIFE binary")
                    self.logger.warning(
                        "RIFE binary not available. Interpolation not possible."
                    )
                    return False
            else:
                self.logger.warning("RIFE binary not found at expected location")
                self.logger.warning(
                    "RIFE binary not available. Interpolation not possible."
                )
                return False

        temp_dir = None
        try:
            import torch
            import torch.nn.functional as F
            import numpy as np
            import cv2
            from pathlib import Path
            import sys
            import os

            # Create temp directory
            temp_dir = tempfile.mkdtemp(prefix="metascan_rife_")
            temp_path = Path(temp_dir)

            if progress_callback:
                progress_callback(5)

            # Extract original frames
            frames_dir = temp_path / "frames"
            frames_dir.mkdir()

            if not self._extract_frames(input_path, frames_dir):
                return False

            if progress_callback:
                progress_callback(20)

            # Get video info
            video_info = self._get_video_info(input_path)

            # Calculate new FPS
            original_fps = video_info["fps"]
            new_fps = original_fps * interpolation_factor

            # Load frames
            frame_files = sorted(list(frames_dir.glob("*.png")))
            if len(frame_files) < 2:
                self.logger.error("Need at least 2 frames for interpolation")
                return False

            if progress_callback:
                progress_callback(30)

            # Use RIFE for proper frame interpolation
            interp_dir = temp_path / "interpolated"
            interp_dir.mkdir()

            # Count input frames to calculate target frame count
            target_frame_count = len(frame_files) * interpolation_factor

            cmd = [
                str(self.rife_bin),
                "-i",
                str(frames_dir),
                "-o",
                str(interp_dir),
                "-m",
                str(
                    self.models_dir
                    / "rife"
                    / "rife-ncnn-vulkan-20221029-macos"
                    / "rife-v4.6"
                ),
                "-n",
                str(target_frame_count),
                "-f",
                "frame_%08d.png",
            ]

            self.logger.info(f"Running RIFE interpolation: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                self.logger.error(f"RIFE failed: {result.stderr}")
                self.logger.warning("Falling back to basic frame blending")
                return self._interpolate_frames_basic(
                    frames_dir,
                    temp_path,
                    interpolation_factor,
                    new_fps,
                    output_path,
                    replace_original,
                    progress_callback,
                )

            if progress_callback:
                progress_callback(85)

            # Update video info with new FPS
            video_info["fps"] = new_fps

            # Compile video
            if not self._compile_video(interp_dir, output_path, video_info):
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

                # Move interpolated to original location
                shutil.move(str(output_path), str(input_path))

            if progress_callback:
                # Check if callback returns False (indicating cancellation)
                if not progress_callback(100):
                    self.logger.info("Processing cancelled by user")
                    return False

            # Log completion with elapsed time
            elapsed_time = time.time() - start_time
            self.logger.info(
                f"Completed frame interpolation: {input_path.name} in {elapsed_time:.1f}s"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to interpolate frames: {e}")
            import traceback

            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False
        finally:
            # Cleanup temp directory
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _interpolate_frames_basic(
        self,
        frames_dir: Path,
        temp_path: Path,
        interpolation_factor: int,
        new_fps: float,
        output_path: Path,
        replace_original: bool,
        progress_callback: Optional[Callable[[float], bool]] = None,
    ) -> bool:
        """Basic frame interpolation using blending (fallback method)."""
        import time

        start_time = time.time()

        try:
            import cv2

            # Load frames
            frame_files = sorted(list(frames_dir.glob("*.png")))

            # Create interpolated frames directory
            interp_dir = temp_path / "interpolated"
            interp_dir.mkdir()

            # Process frame pairs
            total_pairs = len(frame_files) - 1
            output_frame_count = 0

            for i in range(total_pairs):
                if progress_callback:
                    progress = 30 + (i * 50 / total_pairs)
                    progress_callback(progress)

                # Load consecutive frames
                frame1 = cv2.imread(str(frame_files[i]))
                frame2 = cv2.imread(str(frame_files[i + 1]))

                if frame1 is None or frame2 is None:
                    continue

                # Save first frame
                output_frame_path = interp_dir / f"frame_{output_frame_count:08d}.png"
                cv2.imwrite(str(output_frame_path), frame1)
                output_frame_count += 1

                # Generate intermediate frames using basic interpolation
                for j in range(1, interpolation_factor):
                    alpha = j / interpolation_factor
                    interpolated = cv2.addWeighted(frame1, 1 - alpha, frame2, alpha, 0)

                    output_frame_path = (
                        interp_dir / f"frame_{output_frame_count:08d}.png"
                    )
                    cv2.imwrite(str(output_frame_path), interpolated)
                    output_frame_count += 1

            # Save final frame
            if frame_files:
                final_frame = cv2.imread(str(frame_files[-1]))
                if final_frame is not None:
                    output_frame_path = (
                        interp_dir / f"frame_{output_frame_count:08d}.png"
                    )
                    cv2.imwrite(str(output_frame_path), final_frame)

            if progress_callback:
                progress_callback(85)

            # Update video info with new FPS
            video_info = {"fps": new_fps}

            # Compile video
            if not self._compile_video(interp_dir, output_path, video_info):
                return False

            if progress_callback:
                # Check if callback returns False (indicating cancellation)
                if not progress_callback(100):
                    self.logger.info("Processing cancelled by user")
                    return False

            # Log completion with elapsed time
            elapsed_time = time.time() - start_time
            self.logger.info(
                f"Completed basic frame interpolation in {elapsed_time:.1f}s"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed basic frame interpolation: {e}")
            return False

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
                        width, height = img.size  # PIL returns (width, height)
                        return (int(width), int(height))
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

    def _get_upscale_model_info(
        self, scale: int, model_type: str = "general"
    ) -> Tuple[Any, Path]:
        """
        Get the appropriate model and path for upscaling.

        Args:
            scale: Upscaling factor (2 or 4)
            model_type: Type of model ('general' or 'anime')

        Returns:
            Tuple of (model, model_path)
        """
        from basicsr.archs.rrdbnet_arch import RRDBNet

        if model_type == "anime" and scale == 4:
            # Use anime-specific model for 4x upscaling
            model = RRDBNet(
                num_in_ch=3,
                num_out_ch=3,
                num_feat=64,
                num_block=6,  # Anime model uses 6 blocks
                num_grow_ch=32,
                scale=4,
            )
            model_path = self.models_dir / "RealESRGAN_x4plus_anime_6B.pth"
        elif scale == 2:
            # Use standard x2 model
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
            # Use standard x4 model
            model = RRDBNet(
                num_in_ch=3,
                num_out_ch=3,
                num_feat=64,
                num_block=23,
                num_grow_ch=32,
                scale=4,
            )
            model_path = self.models_dir / "RealESRGAN_x4plus.pth"

        return model, model_path

    def _move_to_trash(self, file_path: Path) -> bool:
        """Move a file to platform-specific trash."""
        try:
            import platform
            import subprocess

            system = platform.system()

            if system == "Darwin":  # macOS
                # Use macOS Trash via osascript
                trash_cmd = [
                    "osascript",
                    "-e",
                    f'tell application "Finder" to delete POSIX file "{str(file_path.absolute())}"',
                ]
                result = subprocess.run(trash_cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    self.logger.info(f"Moved to Trash: {file_path}")
                    return True
                else:
                    self.logger.warning(f"Failed to move to Trash: {result.stderr}")

            elif system == "Linux":
                # Try trash-cli first, then fall back to creating backup
                try:
                    result = subprocess.run(
                        ["trash", str(file_path)],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    self.logger.info(f"Moved to Trash: {file_path}")
                    return True
                except (subprocess.CalledProcessError, FileNotFoundError):
                    pass

            elif system == "Windows":
                # Use Windows recycle bin via send2trash
                try:
                    from send2trash import send2trash

                    send2trash(str(file_path))
                    self.logger.info(f"Moved to Recycle Bin: {file_path}")
                    return True
                except ImportError:
                    self.logger.warning("send2trash not available for Windows")
                except Exception as e:
                    self.logger.warning(f"Failed to move to Recycle Bin: {e}")

            # Fallback: create backup instead of deleting
            backup_path = file_path.with_name(
                file_path.stem + "_original" + file_path.suffix
            )
            counter = 1
            while backup_path.exists():
                backup_path = file_path.with_name(
                    f"{file_path.stem}_original_{counter}{file_path.suffix}"
                )
                counter += 1

            shutil.move(str(file_path), str(backup_path))
            self.logger.info(f"Created backup instead of trash: {backup_path}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to move file to trash: {e}")
            return False
