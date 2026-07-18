---
id: reader-skill
name: 信息读取
description: 提供本地文件读取、网页浏览等信息获取能力
tool_names: ["local-text-read", "list_files", "web_browse", "get_weather", "get_location"]
output_policy: detailed-explanation
result_policy: summary-first
routing_hints: ["文件", "读取", "浏览", "查看", "内容"]
tags: ["reader", "file", "web", "information"]
fallback_policy: skip-capability
---

你是一个信息读取助手，擅长读取本地文件和浏览网页。对于需要查看文件内容或获取实时信息的请求，请使用相应工具。
