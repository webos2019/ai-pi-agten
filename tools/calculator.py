"""计算器工具 — 表达式求值（自实现词法分析 + 逆波兰求值，避免 eval 安全风险）"""

import re
from typing import Any

from tool_registry import tool_registry, ChatToolDefinition


def _tokenize(expr: str) -> list[str]:
    tokens: list[str] = []
    current = ""
    for char in expr:
        if char.isspace():
            if current:
                tokens.append(current)
                current = ""
            continue
        if char in "+-*/()":
            if current:
                tokens.append(current)
                current = ""
            tokens.append(char)
            continue
        if char.isdigit() or char == ".":
            current += char
            continue
    if current:
        tokens.append(current)
    return tokens


def _infix_to_postfix(tokens: list[str]) -> list[str]:
    output: list[str] = []
    operators: list[str] = []
    precedence = {"+": 1, "-": 1, "*": 2, "/": 2}

    for token in tokens:
        if re.match(r"^[\d.]+$", token):
            output.append(token)
        elif token == "(":
            operators.append(token)
        elif token == ")":
            while operators and operators[-1] != "(":
                output.append(operators.pop())
            if operators:
                operators.pop()
        else:
            while (
                operators
                and operators[-1] != "("
                and precedence.get(operators[-1], 0) >= precedence.get(token, 0)
            ):
                output.append(operators.pop())
            operators.append(token)

    while operators:
        output.append(operators.pop())
    return output


def _evaluate_postfix(postfix: list[str]) -> float:
    stack: list[float] = []
    for token in postfix:
        if re.match(r"^[\d.]+$", token):
            stack.append(float(token))
        else:
            if len(stack) < 2:
                raise ValueError("无效表达式")
            b = stack.pop()
            a = stack.pop()
            if token == "+":
                stack.append(a + b)
            elif token == "-":
                stack.append(a - b)
            elif token == "*":
                stack.append(a * b)
            elif token == "/":
                if b == 0:
                    raise ValueError("除零错误")
                stack.append(a / b)
            else:
                raise ValueError(f"未知运算符: {token}")
    if len(stack) != 1:
        raise ValueError("无效表达式")
    return stack[0]


def parse_math_expression(expression: str) -> float | str:
    """解析并求值数学表达式"""
    sanitized = re.sub(r"[^0-9+\-*/().\s]", "", expression).strip()
    if not sanitized:
        return "无效表达式"
    try:
        tokens = _tokenize(sanitized)
        postfix = _infix_to_postfix(tokens)
        result = _evaluate_postfix(postfix)
        if not isinstance(result, (int, float)) or result != result:  # NaN check
            return "计算结果无效"
        return result
    except Exception:
        return "表达式错误"


async def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    expression = args.get("expression", "")
    result = parse_math_expression(expression)
    return {"expression": expression, "result": result}


def register():
    tool_registry.register(ChatToolDefinition(
        name="calculator",
        description="执行数学计算，支持加减乘除及括号优先级",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，如: 1+2*3",
                }
            },
            "required": ["expression"],
        },
        execute=execute,
        format_input=lambda args: f"计算: {args.get('expression', '')}",
        result_is_authoritative=True,
        planning_category="action",
        decision_weight=0.9,
        keywords=["计算", "数学", "加减乘除", "表达式", "公式"],
    ))
