import torch
import torch.nn as nn
import torch.nn.functional as F

def absmax_quantize(x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """
    将浮点张量 X 量化为 INT8，并返回缩放因子。

    量化公式:
        scale = 127 / absmax(x)
        x_quant = round(x * scale)
        x_quant = clamp(x_quant, -127, 127)

    反量化公式:
        x_dequant = x_quant / scale

    Args:
        x: 浮点类型的张量

    Returns:
        x_quant: dtype 为 torch.int8 的量化张量
        scale: float 类型的缩放因子
    """
    # ==========================================
    # TODO 1: 计算张量的绝对最大值 absmax
    # ==========================================
    absmax = torch.max(torch.abs(x)).item()

    # 避免除以 0 的情况
    if absmax == 0:
        absmax = 1e-8

    # ==========================================
    # TODO 2: 计算缩放因子 scale (映射到 [-127, 127])
    # ==========================================
    # 把 absmax 映射到 127，所以 scale = 127 / absmax
    scale = 127.0 / absmax

    # ==========================================
    # TODO 3: 量化过程
    # 1. 乘以 scale
    # 2. 四舍五入到整数
    # 3. 限制在 [-127, 127] 范围内
    # 4. 转换为 torch.int8
    # ==========================================
    x_scaled = x * scale
    x_quant = torch.round(x_scaled)
    x_quant = torch.clamp(x_quant, -127, 127)
    x_quant = x_quant.to(torch.int8)

    return x_quant, torch.tensor(scale)


class W8A16Linear(nn.Module):
    """
    Weight-only INT8 量化线性层。

    在内存中，我们存储的是非常微小的 INT8 权重。
    在计算时，我们将权重反量化回 FP16，与同样是 FP16 的输入进行矩阵乘法。

    这种方式虽然没有加速计算，但极大地缓解了从内存读取权重的 Memory-bound (带宽高了 2 倍)。
    """

    def __init__(self, in_features: int, out_features: int):
        super().__init__()
        self.register_buffer("weight_int8", torch.zeros((out_features, in_features), dtype=torch.int8))
        self.register_buffer("scale", torch.tensor(1.0))
        self.bias = nn.Parameter(torch.zeros(out_features))

    def from_float(self, linear_layer: nn.Linear):
        """
        从高精度的 Linear 层中吸收权重并进行 PTQ 量化
        """
        w_quant, scale = absmax_quantize(linear_layer.weight.data)
        self.weight_int8.copy_(w_quant)
        self.scale.copy_(scale)
        if linear_layer.bias is not None:
            self.bias.data.copy_(linear_layer.bias.data)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # ==========================================
        # TODO 4: 反量化与前向传播
        # 1. 将 weight_int8 转换回与输入 x 相同的类型 (如 float32/float16)
        # 2. 除以 self.scale 恢复其数值范围
        # 3. 使用 F.linear 进行标准的矩阵乘法
        # ==========================================

        # 1. 先把 int8 转成和输入一样的浮点类型
        w_fp = self.weight_int8.to(x.dtype)

        # 2. 反量化：除以 scale 恢复原始数值范围
        w_dequant = w_fp / self.scale

        # 3. 标准矩阵乘法
        out = F.linear(x, w_dequant, self.bias)

        return out


# ==========================================
# 测试验证
# ==========================================

def test_absmax_quantize():
    """测试 absmax 量化函数"""
    print("=" * 60)
    print("测试 1: absmax_quantize")
    print("=" * 60)

    # 测试 1: 简单张量
    x = torch.tensor([1.0, 2.0, 3.0, -4.0])
    x_quant, scale = absmax_quantize(x)

    print(f"原始张量: {x}")
    print(f"绝对最大值: {torch.max(torch.abs(x)).item()}")
    print(f"缩放因子 scale: {scale.item():.4f}")
    print(f"量化后 (INT8): {x_quant}")

    # 反量化验证
    x_dequant = x_quant.float() / scale
    print(f"反量化后: {x_dequant}")
    print(f"量化误差: {torch.abs(x - x_dequant).max().item():.6f}")

    # 测试 2: 随机权重矩阵
    print("\n--- 测试权重矩阵 ---")
    w = torch.randn(4, 4)
    w_quant, scale = absmax_quantize(w)
    w_dequant = w_quant.float() / scale

    print(f"原始权重范围: [{w.min():.4f}, {w.max():.4f}]")
    print(f"量化后范围: [{w_quant.min()}, {w_quant.max()}]")
    print(f"反量化后范围: [{w_dequant.min():.4f}, {w_dequant.max():.4f}]")
    print(f"最大误差: {torch.abs(w - w_dequant).max().item():.6f}")


def test_w8a16_linear():
    """测试 W8A16 线性层"""
    print("\n" + "=" * 60)
    print("测试 2: W8A16Linear")
    print("=" * 60)

    # 创建标准 Linear 层
    in_features, out_features = 8, 4
    linear_fp = nn.Linear(in_features, out_features)
    linear_fp.weight.data = torch.randn(out_features, in_features)
    linear_fp.bias.data = torch.randn(out_features)

    # 创建 W8A16 层并量化
    linear_w8 = W8A16Linear(in_features, out_features)
    linear_w8.from_float(linear_fp)

    print(f"FP16 权重 dtype: {linear_fp.weight.dtype}")
    print(f"INT8 权重 dtype: {linear_w8.weight_int8.dtype}")
    print(f"INT8 权重 shape: {linear_w8.weight_int8.shape}")
    print(f"缩放因子: {linear_w8.scale.item():.4f}")

    # 显存对比
    fp16_mem = linear_fp.weight.element_size() * linear_fp.weight.nelement()
    int8_mem = linear_w8.weight_int8.element_size() * linear_w8.weight_int8.nelement()
    print(f"\n显存占用对比:")
    print(f"  FP16 权重: {fp16_mem} bytes")
    print(f"  INT8 权重: {int8_mem} bytes")
    print(f"  压缩比: {fp16_mem / int8_mem:.1f}x")

    # 前向传播对比
    x = torch.randn(2, in_features)

    out_fp = linear_fp(x)
    out_w8 = linear_w8(x)

    print(f"\n前向传播结果对比:")
    print(f"  FP16 输出: {out_fp[0]}")
    print(f"  W8A16 输出: {out_w8[0]}")
    print(f"  最大误差: {torch.abs(out_fp - out_w8).max().item():.6f}")

    # 验证误差很小
    assert torch.abs(out_fp - out_w8).max().item() < 0.1
    print("\n[OK] W8A16 量化测试通过！")


if __name__ == "__main__":
    test_absmax_quantize()
    test_w8a16_linear()
