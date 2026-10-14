"""短语向量编码。

Qwen3-Embedding 封装，按官方示例取最后一个有效 token 池化。编码器缓存可在每个根
处理后收缩到闭集词表向量，避免开放短语逐根累积；同一根内的向量由归一化器的局部
缓存复用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

DEFAULT_BATCH_SIZE = 64
DEFAULT_MAX_LENGTH = 128

Vector = tuple[float, ...]


def last_token_pool(last_hidden_states: Any, attention_mask: Any) -> Any:
    """按 Qwen3-Embedding 官方示例取最后一个有效 token。"""
    left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    import torch

    indices = torch.arange(last_hidden_states.shape[0], device=last_hidden_states.device)
    return last_hidden_states[indices, sequence_lengths]


def cosine(left: Vector, right: Vector) -> float:
    """两个已归一化向量的余弦相似度就是点积。"""
    return sum(a * b for a, b in zip(left, right))


class Qwen3EmbeddingEncoder:
    """批量编码短语，并缓存已编码结果。"""

    def __init__(self, model_path: str | Path, batch_size: int = DEFAULT_BATCH_SIZE,
                 max_length: int = DEFAULT_MAX_LENGTH, device: str | None = None):
        try:
            import torch
            from transformers import Qwen2TokenizerFast, Qwen3Model
        except ImportError as error:  # pragma: no cover - 依赖缺失时的明确报错
            raise RuntimeError("需要 Qwen3-Embedding 对应的 PyTorch 与 transformers") from error
        model_path = Path(model_path)
        if not model_path.is_dir():
            raise FileNotFoundError(f"找不到本地 Qwen3-Embedding 模型目录: {model_path}")
        self.model_path = str(model_path)
        self.torch = torch
        self.batch_size = batch_size
        self.max_length = max_length
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.tokenizer = Qwen2TokenizerFast.from_pretrained(
            str(model_path), padding_side="left", local_files_only=True
        )
        self.model = Qwen3Model.from_pretrained(str(model_path), local_files_only=True)
        self.model.to(self.device)
        self.model.eval()
        self._cache: dict[str, Vector] = {}

    def encode(self, phrases: Iterable[str]) -> dict[str, Vector]:
        """返回 {短语: 向量}；已编码过的短语直接命中缓存。"""
        unique = sorted({phrase for phrase in phrases if phrase})
        missing = [phrase for phrase in unique if phrase not in self._cache]
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start:start + self.batch_size]
            tokens = self.tokenizer(batch, padding=True, truncation=True,
                                    max_length=self.max_length, return_tensors="pt")
            tokens = {key: value.to(self.device) for key, value in tokens.items()}
            with self.torch.no_grad():
                hidden = self.model(**tokens).last_hidden_state
                pooled = last_token_pool(hidden, tokens["attention_mask"])
                pooled = self.torch.nn.functional.normalize(pooled.float(), p=2, dim=1)
            for phrase, vector in zip(batch, pooled.cpu().tolist()):
                self._cache[phrase] = tuple(float(value) for value in vector)
        return {phrase: self._cache[phrase] for phrase in unique}

    def retain_cache(self, phrases: set[str]) -> None:
        """根处理完后仅保留闭集词表向量；开放短语不会跨根累积。"""
        for phrase in list(self._cache):
            if phrase not in phrases:
                del self._cache[phrase]

    @property
    def cache_size(self) -> int:
        return len(self._cache)
