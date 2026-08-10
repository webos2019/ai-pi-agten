import torch

a = torch.tensor(1.0)

b = torch.tensor(1.0, requires_grad=True)

c = b * 2 + 1

# `requires_grad` 只管要不要追踪，`grad_fn` 只管它是不是前面运算生成的结果。

print('a.requires_grad =', a.requires_grad, '| a.grad_fn =', a.grad_fn)

print('b.requires_grad =', b.requires_grad, '| b.grad_fn =', b.grad_fn)

print('c.grad_fn =', type(c.grad_fn).__name__)

print('a.is_leaf =', a.is_leaf, '| b.is_leaf =', b.is_leaf, '| c.is_leaf =', c.is_leaf)



leaf = b

print('leaf.is_leaf =', leaf.is_leaf)
