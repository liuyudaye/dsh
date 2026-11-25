import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ReviewFailure(ValueError):
    pass


class ModelUnavailable(ReviewFailure):
    pass


def read_error(error, status, model):
    code = error.get("code") if isinstance(error, dict) else None
    if code == "model_not_found":
        raise ModelUnavailable(f"NewAPI 当前令牌所在分组没有可用模型 {model}。请换用有该模型权限的令牌。")
    if status in (401, 403):
        raise ReviewFailure("NewAPI 拒绝了请求，请检查令牌和模型权限。")
    if status == 429:
        raise ReviewFailure("NewAPI 限流，请稍后重试。")
    raise ReviewFailure(f"NewAPI 请求失败（HTTP {status}）。")


def json_frames(response):
    if "application/json" in response.headers.get("Content-Type", ""):
        yield json.load(response)
        return
    frame = []
    for raw in response:
        line = raw.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            frame.append(line[5:].lstrip(b" "))
        elif not line and frame:
            payload = b"\n".join(frame)
            frame.clear()
            if payload.strip() == b"[DONE]":
                return
            yield json.loads(payload)
    if frame and b"\n".join(frame).strip() != b"[DONE]":
        yield json.loads(b"\n".join(frame))


def chat_json(prompt, payload, model, base_url, step, max_tokens=16384):
    key = os.getenv("NEWAPI_API_KEY")
    if not key:
        raise ReviewFailure("请在服务端配置 NEWAPI_API_KEY，再开始评审。")
    body = {
        "model": model, "stream": True, "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    if model.lower().startswith("deepseek"):
        body["thinking"] = {"type": "disabled"}
    request = Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                               "Accept": "text/event-stream, application/json"})
    parts = []
    chars = 0
    last_progress = 0.0
    finish = None
    try:
        with urlopen(request, timeout=85) as response:
            for chunk in json_frames(response):
                if chunk.get("error"):
                    read_error(chunk["error"], 502, model)
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                finish = choice.get("finish_reason") or finish
                message = choice.get("delta") or choice.get("message") or {}
                content = message.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
                    chars += len(content)
                    now = time.monotonic()
                    if now - last_progress > .4:
                        yield {"type": "progress", "step": step, "characters": chars}
                        last_progress = now
    except HTTPError as error:
        with error:
            try:
                detail = json.load(error).get("error")
            except (ValueError, AttributeError):
                detail = None
        read_error(detail, error.code, model)
    except (URLError, TimeoutError, OSError) as error:
        raise ReviewFailure("NewAPI 连接超时或中断，请稍后重试。") from error
    except (ValueError, TypeError, AttributeError) as error:
        if isinstance(error, ReviewFailure):
            raise
        raise ReviewFailure("NewAPI 返回了无法读取的模型响应。") from error
    if finish == "length":
        raise ReviewFailure("模型输出到达长度上限，请按章节评审这份文档。")
    if finish != "stop" or not parts:
        raise ReviewFailure("模型回复提前结束，请重试。")
    yield {"type": "progress", "step": step, "characters": chars}
    text = "".join(parts).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]+?)\s*```", text, re.I)
    try:
        return json.loads(fenced[1] if fenced else text)
    except ValueError as error:
        raise ReviewFailure("模型回复不是完整的 JSON，请重试。") from error


def finish(stream):
    while True:
        try:
            next(stream)
        except StopIteration as done:
            return done.value
