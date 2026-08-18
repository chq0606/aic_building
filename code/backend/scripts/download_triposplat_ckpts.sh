#!/bin/bash
# ============================================================================
# download_triposplat_ckpts.sh - 下载 TripoSplat 5 个权重文件
# ----------------------------------------------------------------------------
# TripoSplat 单图重建需要 5 个 safetensors 权重 (~3.6GB 总大小):
#   - diffusion_models/diffusion_model.safetensors
#   - vae/vae.safetensors
#   - clip_vision/clip_vision.safetensors
#   - background_removal/background_removal.safetensors
#   - (其他附件 tokenizer/tokenizer.json 等)
#
# 来源: HuggingFace Tripo-Splat/Tripo-Splat 仓库
# 国内加速: 走 hf-mirror.com (HF_ENDPOINT 环境变量)
#
# 用法:
#   bash code/backend/scripts/download_triposplat_ckpts.sh
#   # 或指定目录:
#   bash code/backend/scripts/download_triposplat_ckpts.sh /path/to/ckpts
#
# 下载完后 docker-compose 会挂载 code/backend/storage/triposplat/ 到
# triposplat_worker 容器, 启动时 TripoSplatPipeline 自动加载.
# ============================================================================
set -e

# 默认下载目录 (跟 config.py 的 triposplat_ckpts_dir 对齐)
CKPTS_DIR="${1:-code/backend/storage/triposplat/ckpts}"

# 检查 huggingface_hub 是否装了
if ! python -c "import huggingface_hub" 2>/dev/null; then
    echo ">>> 安装 huggingface_hub..."
    pip install --quiet huggingface_hub
fi

# 国内镜像加速 (海外机器可以注释掉这一行)
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

mkdir -p "$CKPTS_DIR"

echo "================================================================"
echo "下载 TripoSplat 权重到: $CKPTS_DIR"
echo "  - 来源: HuggingFace Tripo-Splat/Tripo-Splat"
echo "  - 镜像: $HF_ENDPOINT"
echo "  - 大小: ~3.6GB (首次下载耗时 5-30min, 视网络)"
echo "================================================================"

python -c "
from huggingface_hub import snapshot_download
import os

print('开始下载...')
snapshot_download(
    repo_id='Tripo-Splat/Tripo-Splat',
    local_dir=os.environ.get('CKPTS_DIR_OVERRIDE', '$CKPTS_DIR'),
    local_dir_use_symlinks=False,
    max_workers=4,
)
print('下载完成')
"

echo ""
echo "================================================================"
echo "权重文件已就绪. 启用 GPU 重建功能:"
echo "  make download-ckpts    # (已执行)"
echo "  make gpu-up            # 启动含 triposplat_worker 的全服务"
echo "================================================================"
