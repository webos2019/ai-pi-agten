data =list(range(100))
#手写求和
mannual = 0
for v in data: manual += v
#内置sum
builtin =sum(data)
assert manual == builtin == 499500
#成员判断
big = set(range(10000))
assert 9999 in big
print("All Pass!!!")