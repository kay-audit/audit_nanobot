# Сборка PyTorch из source под CUDA 12.4

## Зачем это нужно

PyTorch выпускает готовые wheels под следующие комбинации:

| CUDA | Python | Доступность |
|---|---|---|
| cu124 | cp310/311/312 | **только до torch 2.6** |
| cu126/128/129 | cp310/311/312/313 | до torch 2.8+ |
| cpu | любой | всегда |

Для **torch ≥ 2.7** wheel'ы под **cu124 больше не выпускаются** (PyTorch официально переключился на cu126+). Если на сервере стоит драйвер NVIDIA 12.4 и нет возможности обновить — единственный путь получить torch 2.7+/cu124 — **собрать из исходников**.

## Когда это нужно

- Серверная инфраструктура требует **torch 2.7+** (например, для `sglang==0.4.10.post2` который требует `torch==2.13.0`)
- На сервере установлен **CUDA driver 12.4** (например, NVIDIA-Linux-x86_64-550.x driver)
- Невозможно обновить driver до 12.6+ (политика безопасности, отсутствие прав, или vendor lock)
- Невозможно использовать `--index-url https://download.pytorch.org/whl/cu126` без совместимого runtime

---

## Пре-реквизиты (Ubuntu 22.04/24.04)

Минимум 50 ГБ свободного места на диске (исходники + build артефакты), 16+ ГБ RAM.

```bash
sudo apt update && sudo apt install -y \
    build-essential cmake ninja-build git wget curl \
    libopenblas-dev libomp-dev ccache

# CUDA toolkit 12.4 (для nvcc и headers)
# Если toolkit не установлен — поставить через NVIDIA repo
sudo apt-key adv --fetch-keys https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/3bf863cc.pub
sudo add-apt-repository "deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/ /"
sudo apt update
sudo apt install -y cuda-toolkit-12-4
```

Проверка:

```bash
nvcc --version              # должен показать Cuda compilation tools 12.x
nvidia-smi                  # должен показать Driver Version >= 550, CUDA Version: 12.4
python3 --version           # 3.10 / 3.11 / 3.12
```

---

## Вариант 1: В Docker-контейнере (рекомендую)

Изолированная сборка, не трогает хост-систему. Сборка занимает 1.5–4 часа в зависимости от CPU.

### 1.1 Dockerfile.build-torch-cu124

```dockerfile
FROM nvidia/cuda:12.4.1-devel-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    build-essential cmake ninja-build git wget curl ca-certificates \
    libopenblas-dev libomp-dev ccache \
    && rm -rf /var/lib/apt/lists/*

ENV PATH=/usr/local/cuda-12.4/bin:/usr/local/nvidia/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/cuda-12.4/lib64:${LD_LIBRARY_PATH}

# Faster rebuilds via ccache
ENV CCACHE_DIR=/root/.ccache
ENV CCACHE_MAXSIZE=10G
RUN mkdir -p $CCACHE_DIR

# Build directory
WORKDIR /build
RUN git clone --depth 1 --branch v2.7.1 --recursive https://github.com/pytorch/pytorch.git
WORKDIR /build/pytorch

# Make sure submodules are present (recursive clone may miss third_party)
RUN git submodule update --init --recursive

# Build command — все важные переменные здесь
ENV USE_CUDA=1
ENV USE_CUDNN=1
ENV USE_NCCL=1
ENV USE_MKLDNN=0
ENV TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0"  # A100, RTX 3050/3090/4090, H100
ENV BUILD_TEST=0
ENV USE_DISTRIBUTED=0
ENV USE_MPI=0
ENV USE_GLOO=0
ENV PYTORCH_BUILD_VERSION=2.7.1
ENV PYTORCH_BUILD_NUMBER=1
ENV CMAKE_BUILD_TYPE=Release
ENV MAX_JOBS=$(nproc)
ENV CUDA_TOOLKIT_ROOT_DIR=/usr/local/cuda-12.4

RUN python3.12 -m pip install --no-cache-dir -U pip setuptools wheel
RUN python3.12 setup.py bdist_wheel 2>&1 | tee /build/build.log
# wheel появится в /build/pytorch/dist/torch-2.7.1-cp312-cp312-linux_x86_64.whl

# Запаковать wheel
RUN mkdir -p /out
RUN cp /build/pytorch/dist/*.whl /out/
```

### 1.2 Запуск сборки

```bash
docker build -f Dockerfile.build-torch-cu124 \
  -t pytorch-builder:2.7.1-cu124 \
  --progress=plain .

# Извлечь wheel
docker run --rm -v $(pwd)/wheels:/out pytorch-builder:2.7.1-cu124 sh -c "ls -lh /out/"
# Скопировать wheel в постоянное место
docker run --rm -v $(pwd)/wheels:/wheels pytorch-builder:2.7.1-cu124 \
    sh -c "cp /out/*.whl /wheels/"
ls -lh wheels/
```

Размер wheel: **~700 МБ** (содержит cuDNN, NCCL и shared-библиотеки).

---

## Вариант 2: Нативная сборка на сервере (Ubuntu 24.04)

```bash
# 1. Установить пре-реквизиты (см. выше)

# 2. Клонировать PyTorch
git clone --depth 1 --branch v2.7.1 --recursive https://github.com/pytorch/pytorch.git
cd pytorch
git submodule update --init --recursive

# 3. Установить Python build deps
python3.12 -m pip install --user -U pip setuptools wheel cmake ninja

# 4. Установить переменные окружения
export CUDA_HOME=/usr/local/cuda-12.4
export PATH=$CUDA_HOME/bin:$PATH
export LD_LIBRARY_PATH=$CUDA_HOME/lib64:$LD_LIBRARY_PATH
export TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0"  # A100(8.0), RTX3050(8.6), RTX4090(8.9), H100(9.0)

# 5. Запустить сборку (1.5–4 часа, nproc = кол-во CPU cores)
MAX_JOBS=$(nproc) USE_CUDA=1 USE_CUDNN=1 USE_NCCL=1 \
    USE_MKLDNN=0 BUILD_TEST=0 USE_DISTRIBUTED=0 USE_MPI=0 USE_GLOO=0 \
    PYTORCH_BUILD_VERSION=2.7.1 PYTORCH_BUILD_NUMBER=1 \
    python3.12 setup.py bdist_wheel 2>&1 | tee /tmp/build.log

# 6. Wheel в dist/
ls -lh dist/torch-2.7.1*.whl
```

> ⚠️ **Внимание**: при `BUILD_TEST=0` wheel **не будет содержать тестов**. Это нормально для production.

---

## Установка собранного wheel

После получения `torch-2.7.1-cp312-cp312-linux_x86_64.whl` на сервере:

```bash
# В чистом venv:
python3.12 -m venv /opt/sglang-venv
source /opt/sglang-venv/bin/activate

# Установить ТОЛЬКО torch+torchvision+torchaudio своего производства
pip install torch-2.7.1-cp312-cp312-linux_x86_64.whl
pip install torchvision==0.22.1 torchaudio==2.7.1 --no-deps  # затем ставим версии PyPI, без переустановки torch

# Затем runtime deps из docs/sglang/requirements-server.txt
pip install -r requirements-server.txt

# Проверка
python -c "import torch; print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())"
# Должно быть: torch: 2.7.1 True
```

---

## Проверка правильной сборки

```python
import torch
print("torch:", torch.__version__)                          # 2.7.1
print("CUDA available:", torch.cuda.is_available())          # True
print("CUDA version:", torch.version.cuda)                  # 12.4
print("cuDNN version:", torch.backends.cudnn.version())     # 9.x
print("Device:", torch.cuda.get_device_name(0))             # NVIDIA A100 / RTX 3050 / etc
print("Compute cap:", torch.cuda.get_device_capability(0))   # (8, 0) / (8, 6) / etc
print("Total VRAM:", torch.cuda.get_device_properties(0).total_memory / 1e9, "GB")
```

Если `torch.version.cuda == 12.4` — сборка под нужный CUDA. Если `11.8` — случайно подхватился старый toolkit.

---

## Частые ошибки и решения

### 1. `nvcc fatal: Unsupported gpu architecture 'compute_xx'`

```bash
# Проверить compute capability целевой GPU
nvidia-smi --query-gpu=compute_cap --format=csv
# Должно быть в TORCH_CUDA_ARCH_LIST
export TORCH_CUDA_ARCH_LIST="8.6"  # для RTX 3050
```

### 2. `Could NOT find CUDA (missing: CUDA_CUDART_LIBRARY)`

```bash
# Установить CUDA toolkit 12.4 (devel, не runtime)
sudo apt install cuda-toolkit-12-4
export CUDA_HOME=/usr/local/cuda-12.4
export PATH=$CUDA_HOME/bin:$PATH
```

### 3. `fatal error: cuda_runtime.h: No such file or directory`

Headers не найдены. Проверить:

```bash
ls /usr/local/cuda-12.4/include/cuda_runtime.h
# Должен быть файл. Если нет — toolkit не установлен, поставить.
```

### 4. OOM во время сборки

Build torch ест 8-16 ГБ RAM на многопроцессорной сборке. Уменьшить:

```bash
export MAX_JOBS=4  # или 2 для маленьких машин
```

### 5. Build идёт >3 часов

Включить ccache:

```bash
apt install ccache
export PATH=/usr/lib/ccache:$PATH
# Теперь повторные сборки за секунды
```

### 6. `ImportError: /usr/lib/x86_64-linux-gnu/libstdc++.so.6: version 'GLIBCXX_3.4.30' not found`

Libstdc++ в системе старая. Поставить GCC 11+:

```bash
sudo apt install -y gcc-11 g++-11
export CC=gcc-11 CXX=g++-11
```

### 7. Долгое скачивание cuDNN/NCCL при сборке

PyTorch во время `setup.py` скачивает cuDNN и NCCL precompiled headers. Заменить на системные:

```bash
export USE_SYSTEM_NCCL=1
# Установить libnccl2 из apt (NCCL repo)
```

---

## Совместимость wheel'а с другими пакетами

Собранный `torch-2.7.1+cu124` wheel будет работать со всеми пакетами, которые проверяют **только** `torch.__version__`:

- `transformers` — без проблем
- `tokenizers` — без проблем
- `numpy`, `scipy` — без проблем
- `huggingface_hub` — без проблем

Проблемы могут быть у пакетов, которые компилируют **CUDA extensions под конкретную CUDA runtime**:
- `xformers`, `flash-attn`, `flashinfer-python` — могут требовать переустановки после смены torch
- `vllm`, `sglang` — обычно имеют свои wheel'ы под cu124 / cu126 / cu128

Для нашего случая (`sglang 0.4.10-post2`):
```bash
pip install --no-deps sglang==0.4.10.post2
pip install sgl-kernel==0.2.8  # содержит precompiled CUDA kernels
pip install flashinfer-python==0.6.18.post1
# Все эти wheel'ы работают с любым CUDA >= 12.4 runtime
```

---

## TL;DR — минимальный скрипт для сервера

```bash
# 1. На сервере с CUDA 12.4 driver:
sudo apt install -y cuda-toolkit-12-4 python3.12-venv git build-essential cmake ninja-build

# 2. Собрать wheel (~2 часа):
cd /tmp
git clone --depth 1 --branch v2.7.1 --recursive https://github.com/pytorch/pytorch.git
cd pytorch
MAX_JOBS=$(nproc) \
    USE_CUDA=1 USE_CUDNN=1 USE_NCCL=1 \
    TORCH_CUDA_ARCH_LIST="8.0;8.6;9.0" \
    PYTORCH_BUILD_VERSION=2.7.1 PYTORCH_BUILD_NUMBER=1 \
    BUILD_TEST=0 USE_DISTRIBUTED=0 \
    python3.12 setup.py bdist_wheel

# 3. Поставить собранный wheel:
python3.12 -m venv /opt/sglang-venv
source /opt/sglang-venv/bin/activate
pip install dist/torch-2.7.1*.whl
pip install torchvision==0.22.1 torchaudio==2.7.1 --no-deps
pip install -r /path/to/docs/sglang/requirements-server.txt

# 4. Запустить sglang с Qwen3:
bash /path/to/run_sglang.sh
```

---

## Полезные ссылки

- PyTorch source build instructions: https://github.com/pytorch/pytorch#from-source
- CUDA toolkit archive: https://developer.nvidia.com/cuda-12-4-0-download-archive
- GPU compute capabilities: https://developer.nvidia.com/cuda-gpus
- DockerHub CUDA images: https://hub.docker.com/r/nvidia/cuda/tags (12.4.1-devel-ubuntu24.04)
- pytorch builder wheels (pre-built): https://download.pytorch.org/whl/
