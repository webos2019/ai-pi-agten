
---
name: wechat-articles
description: >
  读取微信公众号文章。使用 Hermes 的 browser 工具访问和读取微信文章，
  支持总结、提取内容、分析等多种操作。
---

# 微信公众号文章读取工具

使用 Hermes 的 browser 工具读取微信公众号文章的方法。

## 概述

微信公众号文章可以通过两种方式读取：

### 方式一：使用 browser 工具（推荐）
微信公众号文章可以通过浏览器工具直接访问和读取。微信文章通常以以下格式呈现：

- 文章链接格式：`https://mp.weixin.qq.com/s/xxxxx`
- 需要浏览器工具来渲染 JavaScript 内容

### 方式二：使用 curl 命令（快速读取）
对于不需要 JavaScript 渲染的文章，可以直接使用 curl 命令读取：

```bash
curl -s -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' 'https://mp.weixin.qq.com/s/文章 ID'
```

**优点**:
- 快速，不需要启动浏览器
- 可以提取文章的元数据（标题、作者、描述等）
- 绕过微信的部分安全验证

**注意事项**:
- 某些需要 JavaScript 渲染的内容可能无法获取
- 如果触发微信验证，可能需要更换 User-Agent

## 使用方法

### 1. 直接读取文章

当提供微信公众号链接时，Hermes 会自动使用 browser 工具：

```
读取这篇文章：https://mp.weixin.qq.com/s/xxxxx
```

### 2. 提取特定内容

```
分析这篇文章的主要内容
```

```
总结这篇文章的关键观点
```

### 3. 批量读取

```
读取这几篇文章：
- https://mp.weixin.qq.com/s/xxx
- https://mp.weixin.qq.com/s/yyy
```

## curl 读取方法

### 基本读取
```bash
curl -s -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' 'https://mp.weixin.qq.com/s/{文章 ID}'
```

### 提取关键信息
```bash
curl -s -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' 'https://mp.weixin.qq.com/s/{文章 ID}' | grep -E '<meta property="og:|<h1|<h2|<p>'
```

### 提取文章元数据
```bash
curl -s -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' 'https://mp.weixin.qq.com/s/{文章 ID}' | grep -E '<meta property="og:(title|url|image|description|author)"'
```

### 提取正文内容
```bash
curl -s -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' 'https://mp.weixin.qq.com/s/{文章 ID}' | grep -A 1000 'id="js_content"'
```

## 浏览器配置

在 `~/.hermes/config.yaml` 中配置 browser 设置：

```yaml
browser:
  allow_private_urls: false
  command_timeout: 30
  inactivity_timeout: 120
```

## 工作流程

1. **接收链接**: 用户分享微信公众号文章链接
2. **浏览器访问**: 使用 `browser_navigate` 访问链接
3. **内容提取**: 使用 `browser_snapshot` 获取页面内容
4. **内容处理**: 提取标题、作者、发布时间、正文内容
5. **格式化输出**: 根据用户需求格式化内容

## 输出格式

### 基础信息
- 文章标题
- 公众号名称
- 作者
- 发布时间
- 阅读数/点赞数

### 正文内容
- 完整文章文本
- 段落结构
- 图片描述（如有）

### 分析结果（可选）
- 内容摘要
- 关键观点
- 情感分析

## 常见问题

**Q: 文章无法加载？**
A: 检查网络连接，确认链接有效，尝试手动在浏览器中打开

**Q: 内容不完整？**
A: 尝试增加 `command_timeout` 设置，或分多次读取

**Q: 图片无法显示？**
A: browser 工具会提取图片 URL，可以单独分析图片内容

**Q: 需要登录才能查看？**
A: 某些公众号文章需要登录，browser 工具会尝试使用保存的 Cookie

**Q: curl 方法读取失败？**
A: 如果返回"环境异常"提示，说明触发了微信验证。尝试：
1. 更换 User-Agent
2. 使用 browser 工具代替
3. 等待一段时间后再试

**Q: 如何提取文章标题和作者？**
A: 使用以下命令：
```bash
curl -s -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' 'https://mp.weixin.qq.com/s/{文章 ID}' | grep -E '<meta property="og:(title|article:author)"'
```

## 示例

```
# 读取并总结
帮我读取这篇文章并总结：https://mp.weixin.qq.com/s/abc123

# 提取关键信息
从这篇文章中提取所有技术名词：https://mp.weixin.qq.com/s/def456

# 比较多篇文章
比较这两篇文章的观点：
- https://mp.weixin.qq.com/s/xxx
- https://mp.weixin.qq.com/s/yyy

# 获取元数据
这篇文章的作者是谁？什么时候发布的？
```

## 注意事项

1. **网络访问**: 确保可以访问微信服务器
2. **登录状态**: 某些文章可能需要微信登录，browser 工具会处理 Cookie
3. **加载时间**: 微信文章可能包含大量图片和 JavaScript，需要等待加载完成
4. **超时设置**: 默认超时为 30 秒，复杂文章可能需要更长时间
5. **隐私**: 某些公众号文章可能包含敏感信息，注意保护隐私