"""
Chronos-2 共享 Pipeline（单例）

prediction 和 validation 工具共用此模块，避免大模型重复加载占用内存。
"""
import logging
import threading

logger = logging.getLogger(__name__)

_pipeline = None
_pipeline_lock = threading.Lock()
_chronos_model_name = "amazon/chronos-2"


def get_pipeline():
    """获取 Chronos2Pipeline 单例（首次调用时加载，后续复用，线程安全）"""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        try:
            import torch
            from chronos import Chronos2Pipeline
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"正在加载 Chronos-2 模型: {_chronos_model_name}，设备: {device}")
            try:
                _pipeline = Chronos2Pipeline.from_pretrained(
                    _chronos_model_name,
                    device_map=device,
                    local_files_only=True,
                )
                logger.info("Chronos-2 使用本地缓存加载")
            except Exception:
                logger.info("本地缓存不可用，尝试下载...")
                _pipeline = Chronos2Pipeline.from_pretrained(
                    _chronos_model_name,
                    device_map=device,
                )
            logger.info("Chronos-2 模型加载完成")
        except Exception as e:
            logger.error(f"Chronos-2 模型加载失败: {e}")
            raise
    return _pipeline
