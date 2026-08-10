import torch


x = torch.tensor(2.0, requires_grad=True)

y = x * x + 3 * x

print('y.item()：', y.item())

# `backward()` 会沿着前向图把梯度写回叶子节点的 `.grad`。

y.backward()

print('x.grad：', x.grad.item())

print('x.is_leaf：', x.is_leaf)
