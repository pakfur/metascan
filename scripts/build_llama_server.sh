#!/usr/bin/env bash
# Build llama-server from source against the LLAMA_CPP_RELEASE pin and
# install it as metascan's local-override binary at data/bin/local/.
#
# Use this when the upstream prebuilt for your platform either doesn't
# exist or doesn't include the accelerator your hardware supports —
# notably Linux (incl. WSL2) with NVIDIA CUDA, since upstream ships no
# Linux CUDA prebuilt for any release tag.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON:-python3}"
RELEASE_TAG="$(
  cd "${REPO_ROOT}" && \
  "${PYTHON_BIN}" -c \
    'from metascan.utils.llama_server import LLAMA_CPP_RELEASE; print(LLAMA_CPP_RELEASE)' \
  2>/dev/null
)" || {
  echo "error: could not read LLAMA_CPP_RELEASE — is your venv activated?" >&2
  echo "       try: source venv/bin/activate" >&2
  exit 1
}

ACCEL="auto"
SKIP_DEPS_CHECK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --accel) ACCEL="$2"; shift 2 ;;
    --accel=*) ACCEL="${1#*=}"; shift ;;
    --skip-deps-check) SKIP_DEPS_CHECK=1; shift ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [--accel auto|cuda|metal|vulkan|hip|cpu] [--skip-deps-check]

Builds llama-server from upstream tag ${RELEASE_TAG} and installs it
into metascan's local-override directory (data/bin/local/).

Options:
  --accel auto         Pick best option from detected hardware (default)
  --accel cuda         NVIDIA GPU via CUDA toolkit
  --accel metal        Apple Silicon GPU
  --accel vulkan       Cross-vendor Vulkan
  --accel hip          AMD GPU via ROCm/HIP
  --accel cpu          CPU-only build
  --skip-deps-check    Bypass preflight (you know what you're doing)
  -h, --help           Show this help

The script preflights every dependency it needs and prints a single
copy-paste install command on failure. See docs/build-llama-server.md.
EOF
      exit 0
      ;;
    *) echo "error: unknown option: $1" >&2; exit 2 ;;
  esac
done

UNAME_S="$(uname -s)"
UNAME_M="$(uname -m)"

if [[ "${ACCEL}" == "auto" ]]; then
  if [[ "${UNAME_S}" == "Darwin" ]]; then
    [[ "${UNAME_M}" == "arm64" ]] && ACCEL="metal" || ACCEL="cpu"
  elif command -v nvidia-smi >/dev/null 2>&1; then
    ACCEL="cuda"
  elif command -v rocminfo >/dev/null 2>&1; then
    ACCEL="hip"
  else
    ACCEL="cpu"
  fi
fi

echo "==> Building llama-server ${RELEASE_TAG} with accelerator: ${ACCEL}"
echo "    Platform: ${UNAME_S} ${UNAME_M}"

# ---------------------------------------------------------------------------
# Preflight: detect package manager, check every prerequisite, suggest a
# single install command for whatever is missing.
# ---------------------------------------------------------------------------

PKG_MGR=""
if command -v apt-get >/dev/null 2>&1; then
  PKG_MGR="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG_MGR="dnf"
elif command -v pacman >/dev/null 2>&1; then
  PKG_MGR="pacman"
elif command -v brew >/dev/null 2>&1; then
  PKG_MGR="brew"
fi

# Per-manager package lists accumulate as we discover what's missing.
APT_PKGS=""
DNF_PKGS=""
PACMAN_PKGS=""
BREW_PKGS=""
MISSING=()

# ``_need_pkg <label> <apt> <dnf> <pacman> <brew>``
# Records that something needs installing and queues the right package
# name for each manager. Empty strings mean "not packaged via this mgr."
_need_pkg() {
  local label="$1"; local apt="$2"; local dnf="$3"; local pac="$4"; local brew="$5"
  MISSING+=("${label}")
  [[ -n "${apt}" ]] && APT_PKGS+=" ${apt}"
  [[ -n "${dnf}" ]] && DNF_PKGS+=" ${dnf}"
  [[ -n "${pac}" ]] && PACMAN_PKGS+=" ${pac}"
  [[ -n "${brew}" ]] && BREW_PKGS+=" ${brew}"
}

_check_cmd() {
  local cmd="$1"; local label="$2"
  command -v "${cmd}" >/dev/null 2>&1 && return 0
  shift 2
  _need_pkg "${label}" "$@"
}

# ``_check_header <pkgconfig-name> <header-relpath> <label> <apt> <dnf> <pac> <brew>``
# Looks up via pkg-config first, falls back to scanning common include dirs.
_check_header() {
  local pcname="$1"; local header="$2"; local label="$3"
  shift 3
  if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists "${pcname}" 2>/dev/null; then
    return 0
  fi
  local d
  for d in /usr/include /usr/local/include /opt/homebrew/include /opt/local/include; do
    [[ -f "${d}/${header}" ]] && return 0
  done
  _need_pkg "${label}" "$@"
}

if [[ "${SKIP_DEPS_CHECK}" -eq 0 ]]; then
  echo "==> Checking prerequisites..."

  _check_cmd cmake "cmake" \
    cmake cmake cmake cmake
  _check_cmd git "git" \
    git git git git
  _check_cmd "${PYTHON_BIN}" "python3" \
    python3 python3 python python@3.12

  # C/C++ toolchain. apt → build-essential, dnf → "Development Tools" group,
  # pacman → base-devel, brew → relies on Xcode CLT (xcode-select --install).
  if ! command -v cc >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1 && \
     ! command -v clang >/dev/null 2>&1; then
    if [[ "${UNAME_S}" == "Darwin" ]]; then
      MISSING+=("C/C++ compiler (run: xcode-select --install)")
    else
      _need_pkg "C/C++ toolchain" \
        build-essential "@development-tools" base-devel ""
    fi
  fi

  # libcurl headers — llama-server's ``common`` lib link-checks for these.
  # apt: libcurl4-openssl-dev, dnf: libcurl-devel, arch: curl, brew: curl
  _check_header libcurl curl/curl.h "libcurl headers" \
    libcurl4-openssl-dev libcurl-devel curl curl

  # pkg-config itself is helpful but not strictly required by cmake.
  if ! command -v pkg-config >/dev/null 2>&1; then
    _need_pkg "pkg-config" pkg-config pkgconf pkgconf pkg-config
  fi

  # Accelerator-specific tools.
  case "${ACCEL}" in
    cuda)
      _check_cmd nvcc "CUDA toolkit (nvcc)" \
        nvidia-cuda-toolkit cuda "" ""
      ;;
    hip)
      _check_cmd hipcc "ROCm/HIP toolkit (hipcc)" \
        rocm-hip-sdk rocm-hip-sdk rocm-hip-runtime ""
      ;;
    vulkan)
      _check_header vulkan vulkan/vulkan.h "Vulkan headers" \
        libvulkan-dev vulkan-headers vulkan-headers molten-vk
      _check_cmd glslc "shaderc (glslc)" \
        glslc shaderc shaderc shaderc
      ;;
  esac

  if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo
    echo "error: missing prerequisites:" >&2
    for m in "${MISSING[@]}"; do echo "  - ${m}" >&2; done
    echo >&2
    case "${PKG_MGR}" in
      apt)
        if [[ -n "${APT_PKGS}" ]]; then
          echo "Install on Debian/Ubuntu/WSL2:" >&2
          echo "  sudo apt-get update && sudo apt-get install -y${APT_PKGS}" >&2
        fi
        ;;
      dnf)
        [[ -n "${DNF_PKGS}" ]] && echo "Install on Fedora/RHEL:" >&2 && \
          echo "  sudo dnf install -y${DNF_PKGS}" >&2
        ;;
      pacman)
        [[ -n "${PACMAN_PKGS}" ]] && echo "Install on Arch:" >&2 && \
          echo "  sudo pacman -S --needed${PACMAN_PKGS}" >&2
        ;;
      brew)
        [[ -n "${BREW_PKGS}" ]] && echo "Install on macOS:" >&2 && \
          echo "  brew install${BREW_PKGS}" >&2
        ;;
      "")
        echo "(no supported package manager detected — install the listed" >&2
        echo " items via your distro's mechanism, then re-run this script.)" >&2
        ;;
    esac
    if [[ "${ACCEL}" == "cuda" && "${PKG_MGR}" == "apt" ]]; then
      echo >&2
      echo "Note: Ubuntu's nvidia-cuda-toolkit package can ship a CUDA version" >&2
      echo "      older than your card needs. For Blackwell (RTX 50-series) you" >&2
      echo "      need CUDA 12.8+ — install from NVIDIA's repo if your build" >&2
      echo "      reports an older toolkit:" >&2
      echo "      https://developer.nvidia.com/cuda-downloads" >&2
    fi
    echo >&2
    echo "Re-run this script after installing. Use --skip-deps-check to bypass." >&2
    exit 1
  fi

  echo "    All prerequisites present."
fi

# ---------------------------------------------------------------------------
# Version sanity-checks (warnings, not errors).
# ---------------------------------------------------------------------------

if [[ "${ACCEL}" == "cuda" ]] && command -v nvcc >/dev/null 2>&1; then
  CUDA_VER="$(nvcc --version 2>/dev/null | sed -n 's/.*release \([0-9.]*\).*/\1/p' | head -1)"
  echo "    Detected CUDA toolkit: ${CUDA_VER:-unknown}"
  if command -v nvidia-smi >/dev/null 2>&1; then
    CAP="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
    [[ -n "${CAP}" ]] && echo "    Detected GPU compute capability: ${CAP}"
    # Blackwell cards report 12.0 / 12.x — they need CUDA 12.8+ or the
    # build either fails or silently produces non-sm_120 binaries that
    # fall back to PTX JIT and run slowly.
    if [[ -n "${CAP}" && "${CAP}" =~ ^12\. && -n "${CUDA_VER}" ]]; then
      CUDA_INT="$(awk -F. -v v="${CUDA_VER}" 'BEGIN{split(v,a,"."); print a[1]*100+a[2]}')"
      if [[ -n "${CUDA_INT}" && "${CUDA_INT}" -lt 1208 ]]; then
        echo
        echo "warning: GPU compute capability ${CAP} (Blackwell) needs CUDA 12.8+" >&2
        echo "         but nvcc is ${CUDA_VER}. The build may still succeed but" >&2
        echo "         won't natively target sm_120 — it'll fall back to PTX for an" >&2
        echo "         older arch and pay JIT time on every model load." >&2
        echo >&2
        echo "         Note: 'nvidia-smi' may show a higher 'CUDA Version' than nvcc." >&2
        echo "         That's the *driver's* max-supported version, not the compiler's." >&2
        echo "         Only nvcc's version determines what GPU archs the build targets." >&2
        echo >&2
        echo "         To install a newer toolkit on Ubuntu/WSL2 see:" >&2
        echo "         docs/build-llama-server.md → 'Upgrading the CUDA toolkit'" >&2
        echo
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------

WORK_DIR="$(mktemp -d -t llama-cpp-build.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

echo "==> Cloning ggml-org/llama.cpp@${RELEASE_TAG}"
git clone --depth 1 --branch "${RELEASE_TAG}" \
  https://github.com/ggml-org/llama.cpp.git "${WORK_DIR}/src"

CMAKE_FLAGS=(
  -DCMAKE_BUILD_TYPE=Release
  # Consolidate every build artefact into build/bin/. By default cmake
  # writes shared libs alongside their target's source dir
  # (libmtmd → build/tools/mtmd/, libllama → build/src/, libggml* →
  # build/ggml/src/, …). The prebuilt release zip flattens them next to
  # the binary; we mirror that so RUNPATH=$ORIGIN finds everything.
  -DCMAKE_RUNTIME_OUTPUT_DIRECTORY="${WORK_DIR}/build/bin"
  -DCMAKE_LIBRARY_OUTPUT_DIRECTORY="${WORK_DIR}/build/bin"
  -DCMAKE_ARCHIVE_OUTPUT_DIRECTORY="${WORK_DIR}/build/bin"
  # Use $ORIGIN-relative RPATH at build time so the binary's RUNPATH is
  # the same in the build tree and after we copy it elsewhere.
  -DCMAKE_BUILD_RPATH_USE_ORIGIN=ON
  -DCMAKE_INSTALL_RPATH='$ORIGIN'
)
case "${ACCEL}" in
  cuda)   CMAKE_FLAGS+=(-DGGML_CUDA=ON) ;;
  metal)  CMAKE_FLAGS+=(-DGGML_METAL=ON) ;;
  vulkan) CMAKE_FLAGS+=(-DGGML_VULKAN=ON) ;;
  hip)    CMAKE_FLAGS+=(-DGGML_HIPBLAS=ON) ;;
  cpu)    : ;;
  *) echo "error: unknown accelerator '${ACCEL}'" >&2; exit 2 ;;
esac

echo "==> Configuring (cmake)..."
cmake -S "${WORK_DIR}/src" -B "${WORK_DIR}/build" "${CMAKE_FLAGS[@]}"

if command -v nproc >/dev/null 2>&1; then
  NPROC="$(nproc)"
else
  NPROC="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
fi

echo "==> Compiling with ${NPROC} jobs (this may take 5-15 minutes)..."
cmake --build "${WORK_DIR}/build" --config Release -j "${NPROC}"

# ---------------------------------------------------------------------------
# Install.
# ---------------------------------------------------------------------------

DEST_DIR="${REPO_ROOT}/data/bin/local"
mkdir -p "${DEST_DIR}"

# Wipe any prior install so stale libs from a different accelerator
# don't get picked up by RUNPATH=$ORIGIN at load time.
rm -f "${DEST_DIR}"/llama-server* \
      "${DEST_DIR}"/lib*.so* \
      "${DEST_DIR}"/lib*.dylib

src_bin_dir="${WORK_DIR}/build/bin"
if [[ ! -x "${src_bin_dir}/llama-server" ]]; then
  echo "error: build did not produce llama-server in ${src_bin_dir}" >&2
  exit 1
fi

# cp -P preserves symlinks so the SONAME chain (libllama.so ->
# libllama.so.0 -> libllama.so.0.0.<n>) survives intact.
cp "${src_bin_dir}/llama-server" "${DEST_DIR}/"
case "${UNAME_S}" in
  Darwin)
    find "${src_bin_dir}" -maxdepth 1 -name 'lib*.dylib' \
      -exec cp -P {} "${DEST_DIR}/" \;
    # Defensive: pick up any dylib cmake placed elsewhere despite the
    # output-dir override (some targets set their own location).
    find "${WORK_DIR}/build" -name 'lib*.dylib' -not -path "${src_bin_dir}/*" \
      -exec cp -P {} "${DEST_DIR}/" \; 2>/dev/null || true
    ;;
  *)
    find "${src_bin_dir}" -maxdepth 1 -name 'lib*.so*' \
      -exec cp -P {} "${DEST_DIR}/" \;
    # Defensive: pick up any .so cmake placed outside build/bin/.
    find "${WORK_DIR}/build" -name 'lib*.so*' -not -path "${src_bin_dir}/*" \
      -exec cp -P {} "${DEST_DIR}/" \; 2>/dev/null || true
    ;;
esac
chmod +x "${DEST_DIR}/llama-server"

# Verify the binary actually loads — catches missing-lib problems now
# instead of when the user clicks Activate and gets a 500 from the
# backend. ``--version`` is a cheap way to trigger the dynamic linker.
echo
echo "==> Verifying binary..."
if ! "${DEST_DIR}/llama-server" --version >/dev/null 2>"${WORK_DIR}/verify.err"; then
  echo "error: llama-server failed to start. Linker error follows:" >&2
  echo >&2
  cat "${WORK_DIR}/verify.err" >&2
  echo >&2
  if [[ "${UNAME_S}" != "Darwin" ]] && command -v ldd >/dev/null 2>&1; then
    echo "ldd output (missing libs marked 'not found'):" >&2
    ldd "${DEST_DIR}/llama-server" 2>&1 | grep -E 'not found|=>' | head -25 >&2 || true
    echo >&2
  fi
  echo "Files present in ${DEST_DIR}:" >&2
  ls -1 "${DEST_DIR}" >&2
  echo >&2
  echo "If a 'lib*.so' file from llama.cpp's build is missing, please file" >&2
  echo "an issue — the build script's output-consolidation flags should have" >&2
  echo "caught this. Workaround: copy missing libs from ${WORK_DIR}/build/" >&2
  echo "(the temp build dir has been cleaned up by now; rebuild and inspect" >&2
  echo "before the script's trap fires by editing the trap command)." >&2
  exit 1
fi

echo "==> Installed to: ${DEST_DIR}/llama-server"
"${DEST_DIR}/llama-server" --version 2>&1 | head -5 || true
echo
echo "==> Restart the metascan backend to pick up the local binary."
echo "    The runtime will prefer ${DEST_DIR}/llama-server over the"
echo "    bundled binary at ${REPO_ROOT}/data/bin/llama-server."
