#!/usr/bin/env python3
"""
TikHub MCP Server — 抖音关键词搜索 + 视频转文字
工具：search_douyin_videos / get_douyin_video_info / transcribe_douyin_video
"""

import os
import re
import json
import datetime
import time
import hashlib
from urllib.parse import urlencode
import httpx
import dashscope
from dashscope.audio.asr import Transcription
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("TikHub Douyin Search")

BASE_URL = "https://api.tikhub.io"


def _api_key() -> str:
    return os.getenv("TIKHUB_API_KEY", "")


def _dashscope_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_api_key()}"}

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1"
)


def _aweme_id_from_url(url: str) -> str:
    """从各种形式的抖音 URL 中提取 aweme_id"""
    # 标准格式: https://www.douyin.com/video/1234567890
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    # 纯数字
    if url.strip().isdigit():
        return url.strip()
    return ""


def _get_play_url_from_tikhub(aweme_id: str) -> str:
    """用 TikHub fetch_one_video 拿无水印播放地址"""
    with httpx.Client(timeout=20) as c:
        r = c.get(
            f"{BASE_URL}/api/v1/douyin/web/fetch_one_video",
            params={"aweme_id": aweme_id},
            headers=_headers(),
        )
        r.raise_for_status()
        aweme = r.json().get("data", {}).get("aweme_detail") or {}

    # 优先取无水印地址
    urls = aweme.get("video", {}).get("play_addr", {}).get("url_list", [])
    if urls:
        return urls[0].replace("playwm", "play")

    # 备选：bit_rate 列表第一条
    for br in aweme.get("video", {}).get("bit_rate", []):
        u = br.get("play_addr", {}).get("url_list", [""])[0]
        if u:
            return u.replace("playwm", "play")
    return ""


def _extract_videos(business_data: list) -> list:
    """从 business_data 中提取 aweme_info 列表"""
    videos = []
    for item in business_data:
        try:
            aweme = item["data"]["aweme_info"]
        except (KeyError, TypeError):
            continue
        aweme_id = aweme.get("aweme_id", "")
        ts = aweme.get("create_time", 0)
        try:
            date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            date_str = ""
        url = f"https://www.douyin.com/video/{aweme_id}" if aweme_id else ""
        title = aweme.get("desc", "")
        videos.append({
            "aweme_id": aweme_id,
            "url": url,
            "title": f"[{title}]({url})" if url else title,
            "author": aweme.get("author", {}).get("nickname", ""),
            "likes": aweme.get("statistics", {}).get("digg_count", 0),
            "comments": aweme.get("statistics", {}).get("comment_count", 0),
            "shares": aweme.get("statistics", {}).get("share_count", 0),
            "date": date_str,
        })
    return videos


@mcp.tool()
def search_douyin_videos(
    keyword: str,
    count: int = 20,
    sort_by_likes: bool = True,
    page: int = 1,
) -> str:
    """
    搜索抖音视频，返回真实视频链接和数据。

    参数:
    - keyword: 搜索关键词，例如"香港保险"
    - count: 返回数量上限（默认 20，最多受平台每页限制）
    - sort_by_likes: True=按点赞数排序，False=综合排序（默认 True）
    - page: 页码，默认 1

    返回:
    - JSON 字符串，包含视频列表，每条含 url/title/author/likes/date
    """
    if not _api_key():
        return json.dumps({"error": "未设置 TIKHUB_API_KEY 环境变量"}, ensure_ascii=False)

    sort_type = "_1" if sort_by_likes else "_0"

    try:
        with httpx.Client(timeout=20) as c:
            r = c.get(
                f"{BASE_URL}/api/v1/douyin/web/fetch_video_search_result_v2",
                params={
                    "keyword": keyword,
                    "sort_type": sort_type,
                    "publish_time": "_0",
                    "filter_duration": "_0",
                    "page": page,
                },
                headers=_headers(),
            )
            r.raise_for_status()
            data = r.json()

    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:300]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    business_data = data.get("data", {}).get("business_data", [])
    videos = _extract_videos(business_data)[:count]

    return json.dumps({
        "keyword": keyword,
        "sort": "likes" if sort_by_likes else "comprehensive",
        "page": page,
        "count": len(videos),
        "videos": videos,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_douyin_video_info(aweme_id: str) -> str:
    """
    获取单个抖音视频的详情（标题、点赞、作者、播放链接等）。

    参数:
    - aweme_id: 视频 ID，例如 "7585335575953280292"
    """
    if not _api_key():
        return json.dumps({"error": "未设置 TIKHUB_API_KEY 环境变量"}, ensure_ascii=False)

    try:
        with httpx.Client(timeout=20) as c:
            r = c.get(
                f"{BASE_URL}/api/v1/douyin/web/fetch_one_video",
                params={"aweme_id": aweme_id},
                headers=_headers(),
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    aweme = data.get("data", {}).get("aweme_detail", {})
    if not aweme:
        return json.dumps({"error": "未找到视频", "raw": str(data)[:200]}, ensure_ascii=False)

    ts = aweme.get("create_time", 0)
    try:
        date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        date_str = ""

    return json.dumps({
        "aweme_id": aweme_id,
        "url": f"https://www.douyin.com/video/{aweme_id}",
        "title": aweme.get("desc", ""),
        "author": aweme.get("author", {}).get("nickname", ""),
        "likes": aweme.get("statistics", {}).get("digg_count", 0),
        "comments": aweme.get("statistics", {}).get("comment_count", 0),
        "shares": aweme.get("statistics", {}).get("share_count", 0),
        "date": date_str,
        "duration": aweme.get("duration", 0),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def transcribe_douyin_video(
    url: str,
    context: str = "香港保险 储蓄险 分红险 保诚 友邦 宏利",
) -> str:
    """
    将抖音视频内容转为文字，一字不差地返回完整口播文本。

    参数:
    - url: 抖音视频链接（支持 https://www.douyin.com/video/ID 或纯视频 ID）
    - context: 语音识别上下文提示词，有助于提高专业术语识别准确率（可选）

    返回:
    - 完整的视频口播文本
    """
    if not _api_key():
        return json.dumps({"error": "未设置 TIKHUB_API_KEY"}, ensure_ascii=False)
    if not _dashscope_key():
        return json.dumps({"error": "未设置 DASHSCOPE_API_KEY，请在 mcporter.json 中配置"}, ensure_ascii=False)

    # 1. 解析 aweme_id
    aweme_id = _aweme_id_from_url(url)
    if not aweme_id:
        return json.dumps({"error": f"无法从 URL 中提取视频 ID: {url}"}, ensure_ascii=False)

    # 2. 用 TikHub 获取真实播放地址
    try:
        play_url = _get_play_url_from_tikhub(aweme_id)
    except Exception as e:
        return json.dumps({"error": f"获取播放地址失败: {e}"}, ensure_ascii=False)

    if not play_url:
        return json.dumps({"error": "TikHub 未返回播放地址，视频可能已删除或不可访问"}, ensure_ascii=False)

    # 3. 调用 DashScope paraformer-v2 异步转录
    try:
        result = _asr_transcribe(play_url)
    except Exception as e:
        return json.dumps({"error": f"ASR 调用异常: {e}"}, ensure_ascii=False)

    if not result.get("success"):
        return json.dumps({"error": result.get("error")}, ensure_ascii=False)

    return json.dumps({
        "aweme_id": aweme_id,
        "url": f"https://www.douyin.com/video/{aweme_id}",
        "text": result.get("text", ""),
    }, ensure_ascii=False, indent=2)


def _xhs_cookie() -> str:
    return os.getenv("XIAOHONGSHU_COOKIE", "")


def _search_xhs_via_cookie(keyword: str, count: int, sort_type: str, note_type: int, page: int) -> list:
    """直接调小红书 API（TikHub 不可用时的备用方案）"""
    cookie = _xhs_cookie()
    if not cookie:
        raise ValueError("未设置 XIAOHONGSHU_COOKIE 环境变量")

    import random, string

    def _rand_hex(n: int) -> str:
        return "".join(random.choices("0123456789abcdef", k=n))

    headers = {
        "Cookie": cookie,
        "x-t": str(int(time.time() * 1000)),
        "x-b3-traceid": _rand_hex(16),
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.xiaohongshu.com/",
        "Origin": "https://www.xiaohongshu.com",
    }
    payload = {
        "keyword": keyword,
        "page": page,
        "page_size": count,
        "search_id": _rand_hex(32),
        "sort": sort_type,
        "note_type": note_type,
        "ext_flags": [],
        "geo": "",
        "image_formats": ["jpg", "webp", "avif"],
    }
    with httpx.Client(timeout=30) as c:
        r = c.post(
            "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes",
            json=payload,
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise ValueError(f"小红书 API 错误: {data.get('msg', 'unknown')}")
    return data.get("data", {}).get("items", [])


@mcp.tool()
def search_xhs_notes(
    keyword: str,
    count: int = 20,
    sort_type: str = "general",
    note_type: int = 0,
    page: int = 1,
) -> str:
    """
    搜索小红书笔记，返回笔记列表和数据。优先使用 TikHub，不可用时自动切换到直连小红书 API。

    参数:
    - keyword: 搜索关键词，例如"香港保险"
    - count: 返回数量上限（默认 20）
    - sort_type: 排序方式，"general"=综合排序，"time_descending"=最新，"popularity_descending"=最热
    - note_type: 笔记类型，0=全部，1=视频，2=图文
    - page: 页码，默认 1

    返回:
    - JSON 字符串，包含笔记列表，每条含 note_id/url/title/author/likes/comments
    """
    items = []
    source = "tikhub"

    if _api_key():
        try:
            with httpx.Client(timeout=30) as c:
                r = c.get(
                    f"{BASE_URL}/api/v1/xiaohongshu/web/search_notes",
                    params={
                        "keyword": keyword,
                        "page": page,
                        "page_size": count,
                        "sort": sort_type,
                    },
                    headers=_headers(),
                )
                r.raise_for_status()
                data = r.json()
                if "detail" not in data:
                    raw = data.get("data", {})
                    items = raw.get("items") or raw.get("data", {}).get("items", [])
        except Exception:
            pass  # fallback below

    if not items:
        source = "direct"
        try:
            raw_items = _search_xhs_via_cookie(keyword, count, sort_type, note_type, page)
            # 直连 API 的数据结构不同
            for item in raw_items[:count]:
                card = item.get("note_card") or {}
                note_id = item.get("id", "")
                url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
                title = card.get("display_title", "") or card.get("title", "")
                interact = card.get("interact_info", {})
                items.append({
                    "id": note_id,
                    "note_card": {
                        "display_title": title,
                        "user": card.get("user", {}),
                        "interact_info": interact,
                        "type": card.get("type", ""),
                    }
                })
        except Exception as e:
            return json.dumps({"error": f"TikHub 和直连 API 均失败: {e}"}, ensure_ascii=False)

    notes = []
    for item in items[:count]:
        if source == "tikhub":
            # web endpoint: {model_type, note: {id, display_title, user, interact_info, ...}}
            note = item.get("note") or item
            note_id = note.get("id", "")
            url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
            title = note.get("display_title") or note.get("title") or note.get("desc", "")
            interact = note.get("interact_info", {})
            notes.append({
                "note_id": note_id,
                "url": url,
                "title": f"[{title}]({url})" if url else title,
                "author": (note.get("user") or {}).get("nickname", ""),
                "likes": interact.get("liked_count", 0),
                "comments": interact.get("comment_count", 0),
                "note_type": note.get("type", ""),
            })
        else:
            card = item.get("note_card", {})
            note_id = item.get("id", "")
            url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
            title = card.get("display_title", "")
            interact = card.get("interact_info", {})
            notes.append({
                "note_id": note_id,
                "url": url,
                "title": f"[{title}]({url})" if url else title,
                "author": (card.get("user") or {}).get("nickname", ""),
                "likes": interact.get("liked_count", 0),
                "comments": interact.get("comment_count", 0),
                "note_type": card.get("type", ""),
            })

    return json.dumps({
        "keyword": keyword,
        "sort": sort_type,
        "page": page,
        "count": len(notes),
        "source": source,
        "notes": notes,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_xhs_note_comments(
    note_id: str,
    count: int = 50,
) -> str:
    """
    获取小红书笔记的评论列表。

    参数:
    - note_id: 笔记 ID（24位十六进制字符串）
    - count: 返回评论数量上限（默认 50）

    返回:
    - JSON 字符串，包含评论列表，每条含 content/likes/user_name
    """
    if not _api_key():
        return json.dumps({"error": "未设置 TIKHUB_API_KEY 环境变量"}, ensure_ascii=False)

    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(
                f"{BASE_URL}/api/v1/xiaohongshu/web/get_note_comments",
                params={"note_id": note_id},
                headers=_headers(),
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:300]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    raw_comments = (data.get("data") or {}).get("data", {}).get("comments", [])
    comments = []
    for c in raw_comments[:count]:
        comments.append({
            "user": (c.get("user") or {}).get("nickname", ""),
            "content": c.get("content", ""),
            "likes": c.get("like_count", 0),
        })

    return json.dumps({
        "note_id": note_id,
        "count": len(comments),
        "comments": comments,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_douyin_video_comments(
    aweme_id: str,
    count: int = 50,
) -> str:
    """
    获取抖音视频的评论列表。

    参数:
    - aweme_id: 视频 ID（纯数字，如 "7585335575953280292"）或完整视频链接
    - count: 返回评论数量上限（默认 50）

    返回:
    - JSON 字符串，包含评论列表，每条含 content/likes/user
    """
    if not _api_key():
        return json.dumps({"error": "未设置 TIKHUB_API_KEY 环境变量"}, ensure_ascii=False)

    aweme_id = _aweme_id_from_url(aweme_id) or aweme_id.strip()

    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(
                f"{BASE_URL}/api/v1/douyin/web/fetch_video_comments",
                params={"aweme_id": aweme_id, "count": count, "cursor": 0},
                headers=_headers(),
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:300]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    raw_comments = data.get("data", {}).get("comments", []) or []
    comments = []
    for c in raw_comments[:count]:
        comments.append({
            "user": (c.get("user") or {}).get("nickname", ""),
            "content": c.get("text", ""),
            "likes": c.get("digg_count", 0),
        })

    return json.dumps({
        "aweme_id": aweme_id,
        "count": len(comments),
        "comments": comments,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_bilibili_video_comments(
    bvid: str,
    count: int = 50,
) -> str:
    """
    获取 B 站视频的评论列表（通过 TikHub API）。

    参数:
    - bvid: 视频 BV 号（如 "BV1SC9iYZEKB"）或完整视频链接
    - count: 返回评论数量上限（默认 50，每页最多 20 条）

    返回:
    - JSON 字符串，包含评论列表，每条含 content/likes/user
    """
    if not _api_key():
        return json.dumps({"error": "未设置 TIKHUB_API_KEY 环境变量"}, ensure_ascii=False)

    m = re.search(r"BV[a-zA-Z0-9]+", bvid)
    if m:
        bvid = m.group(0)

    comments = []
    page_size = min(count, 20)
    pages = (count + 19) // 20

    try:
        with httpx.Client(timeout=30) as c:
            for pn in range(1, pages + 1):
                r = c.get(
                    f"{BASE_URL}/api/v1/bilibili/web/fetch_video_comments",
                    params={"bv_id": bvid, "ps": page_size, "pn": pn},
                    headers=_headers(),
                )
                r.raise_for_status()
                data = r.json()
                replies = (data.get("data") or {}).get("data", {}).get("replies") or []
                for rep in replies:
                    comments.append({
                        "user": (rep.get("member") or {}).get("uname", ""),
                        "content": (rep.get("content") or {}).get("message", ""),
                        "likes": rep.get("like", 0),
                    })
                if len(replies) < page_size or len(comments) >= count:
                    break
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:300]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    return json.dumps({
        "bvid": bvid,
        "count": len(comments[:count]),
        "comments": comments[:count],
    }, ensure_ascii=False, indent=2)


def _note_id_from_xhs_url(url: str) -> str:
    """从各种小红书 URL 中提取 note_id，短链接原样返回供 share_text 参数使用"""
    url = url.strip()
    # 标准格式: /explore/<id> 或 /discovery/item/<id>
    m = re.search(r"/(?:explore|discovery/item)/([a-f0-9]{24})", url)
    if m:
        return m.group(1)
    # 纯 note_id（24位十六进制）
    if re.match(r"^[a-f0-9]{24}$", url):
        return url
    return ""


def _asr_transcribe(audio_url: str) -> dict:
    """通用：提交 DashScope paraformer-v2 异步转录，返回 {success, text, error}"""
    task = Transcription.async_call(
        api_key=_dashscope_key(),
        model="paraformer-v2",
        file_urls=[audio_url],
        language_hints=["zh", "yue"],
    )
    if task.status_code != 200:
        return {"success": False, "error": f"提交失败: {task.message}"}

    task_id = task.output.task_id
    time.sleep(3)  # 先等3秒再开始轮询
    for _ in range(30):
        time.sleep(4)
        result = Transcription.fetch(api_key=_dashscope_key(), task=task_id)
        status = result.output.task_status
        if status == "SUCCEEDED":
            tr_url = result.output.results[0].get("transcription_url", "")
            if not tr_url:
                return {"success": True, "text": ""}
            with httpx.Client(timeout=20) as c:
                tr_data = c.get(tr_url).json()
            text = " ".join(
                seg.get("text", "")
                for t in tr_data.get("transcripts", [])
                for seg in t.get("sentences", [])
            )
            return {"success": True, "text": text}
        if status in ("FAILED", "CANCELED"):
            return {"success": False, "error": f"任务失败: {result.output}"}
    return {"success": False, "error": "转录超时（>120s）"}


@mcp.tool()
def transcribe_xhs_video(
    url: str,
    context: str = "香港保险 储蓄险 分红险 保诚 友邦 宏利",
) -> str:
    """
    将小红书视频内容转为文字，返回完整口播文本。

    参数:
    - url: 小红书笔记链接（支持 https://www.xiaohongshu.com/explore/<id>
           或 https://www.xiaohongshu.com/discovery/item/<id>
           或短链接 http://xhslink.com/...
           或纯 note_id）
    - context: 语音识别上下文提示词（可选）

    返回:
    - 完整的视频口播文本
    """
    if not _api_key():
        return json.dumps({"error": "未设置 TIKHUB_API_KEY"}, ensure_ascii=False)
    if not _dashscope_key():
        return json.dumps({"error": "未设置 DASHSCOPE_API_KEY"}, ensure_ascii=False)

    url = url.strip()
    note_id = _note_id_from_xhs_url(url)

    # 调用 TikHub app/get_note_info（无需登录）获取视频详情
    try:
        params = {"note_id": note_id} if note_id else {"share_text": url}
        with httpx.Client(timeout=20) as c:
            r = c.get(
                f"{BASE_URL}/api/v1/xiaohongshu/app/get_note_info",
                params=params,
                headers=_headers(),
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return json.dumps({"error": f"获取视频详情失败: {e}"}, ensure_ascii=False)

    # 结构：data.data.data[0].note_list[0]
    outer = data.get("data", {}).get("data", [])
    if not outer:
        return json.dumps({"error": "未找到视频笔记", "raw": str(data)[:200]}, ensure_ascii=False)
    note_list = outer[0].get("note_list", []) if isinstance(outer, list) else []
    if not note_list:
        return json.dumps({"error": "note_list 为空，可能是图文笔记"}, ensure_ascii=False)

    note = note_list[0]
    actual_note_id = note.get("id", note_id)

    # 优先取纯音频 m4a（native_voice_info.url）
    audio_url = note.get("native_voice_info", {}).get("url", "")

    # 备选：video.url（带签名的 mp4）
    if not audio_url:
        audio_url = note.get("video", {}).get("url", "")

    if not audio_url:
        return json.dumps({"error": "无法获取音视频地址，可能是图文笔记而非视频"}, ensure_ascii=False)

    # 转录
    try:
        result = _asr_transcribe(audio_url)
    except Exception as e:
        return json.dumps({"error": f"ASR 调用异常: {e}"}, ensure_ascii=False)

    if not result.get("success"):
        return json.dumps({"error": result.get("error")}, ensure_ascii=False)

    return json.dumps({
        "note_id": actual_note_id,
        "url": f"https://www.xiaohongshu.com/explore/{actual_note_id}",
        "text": result.get("text", ""),
    }, ensure_ascii=False, indent=2)


_BILIBILI_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_BILIBILI_HEADERS = {"User-Agent": _BILIBILI_UA, "Referer": "https://www.bilibili.com"}
_WBI_ENC_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]


def _bilibili_wbi_sign(params: dict) -> dict:
    """获取 WBI 签名 key 并对参数签名"""
    with httpx.Client(timeout=10, headers=_BILIBILI_HEADERS) as c:
        r = c.get("https://api.bilibili.com/x/web-interface/nav")
        wbi = r.json()["data"]["wbi_img"]
    img = wbi["img_url"].split("/")[-1].split(".")[0]
    sub = wbi["sub_url"].split("/")[-1].split(".")[0]
    raw = img + sub
    mixin = "".join(raw[i] for i in _WBI_ENC_TAB)[:32]
    params["wts"] = int(time.time())
    s = urlencode(sorted(params.items())) + mixin
    params["w_rid"] = hashlib.md5(s.encode()).hexdigest()
    return params


@mcp.tool()
def search_bilibili_videos(
    keyword: str,
    count: int = 20,
    order: str = "click",
    page: int = 1,
) -> str:
    """
    搜索 B 站视频，返回视频列表和数据。无需 Cookie 或 API Key。

    参数:
    - keyword: 搜索关键词，例如"香港保险"
    - count: 返回数量上限（默认 20，B 站每页最多 20 条）
    - order: 排序方式，"click"=播放量，"likes"=点赞数，"pubdate"=最新发布，"dm"=弹幕数，"stow"=收藏数
    - page: 页码，默认 1

    返回:
    - JSON 字符串，包含视频列表，每条含 bvid/url/title/author/play/likes/pubdate
    """
    try:
        params = _bilibili_wbi_sign({
            "search_type": "video",
            "keyword": keyword,
            "order": order,
            "ps": count,
            "pn": page,
        })
        with httpx.Client(timeout=15, headers=_BILIBILI_HEADERS) as c:
            r = c.get("https://api.bilibili.com/x/web-interface/wbi/search/type", params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    if data.get("code") != 0:
        return json.dumps({"error": data.get("message", "unknown"), "code": data.get("code")}, ensure_ascii=False)

    results = data.get("data", {}).get("result", [])
    videos = []
    for v in results[:count]:
        bvid = v.get("bvid", "")
        pub_ts = v.get("pubdate", 0)
        try:
            pub_date = datetime.datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d")
        except Exception:
            pub_date = ""
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        title = re.sub("<[^>]+>", "", v.get("title", ""))
        videos.append({
            "bvid": bvid,
            "url": url,
            "title": f"[{title}]({url})" if url else title,
            "author": v.get("author", ""),
            "play": v.get("play", 0),
            "likes": v.get("like", 0),
            "danmaku": v.get("video_review", 0),
            "pubdate": pub_date,
        })

    return json.dumps({
        "keyword": keyword,
        "order": order,
        "page": page,
        "count": len(videos),
        "videos": videos,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def search_wechat_channels_videos(
    keyword: str,
    count: int = 20,
) -> str:
    """
    搜索微信视频号视频，返回视频列表。使用 TikHub API。

    参数:
    - keyword: 搜索关键词，例如"香港保险"
    - count: 返回数量上限（默认 20，视频号每次最多返回约 8 条）

    返回:
    - JSON 字符串，包含视频列表，每条含 video_id/title/author/likes/duration/pub_time
    """
    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(
                f"{BASE_URL}/api/v1/wechat_channels/fetch_search_ordinary",
                params={"keywords": keyword},
                headers=_headers(),
            )
            r.raise_for_status()
            data = r.json()

        items = data.get("data", {}).get("items", [])
        videos = []
        for item in items[:count]:
            import re as _re
            title = _re.sub("<[^>]+>", "", item.get("title", ""))
            source = item.get("source", {})
            author = source.get("title", "")
            like_num = item.get("likeNum", "0")
            try:
                likes = int(like_num)
            except (ValueError, TypeError):
                likes = 0
            video_id = item.get("hashDocID", "")
            videos.append({
                "video_id": video_id,
                "title": title,
                "author": author,
                "likes": likes,
                "duration": item.get("duration", ""),
                "pub_time": item.get("dateTime", ""),
                "pub_timestamp": item.get("pubTime", 0),
                "note": "视频号内容仅限微信内查看，无网页链接",
            })

        return json.dumps({
            "keyword": keyword,
            "count": len(videos),
            "videos": videos,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def get_wechat_channel_comments(
    video_id: str,
    count: int = 50,
) -> str:
    """
    获取微信视频号视频的评论列表。video_id 为 search_wechat_channels_videos 返回的 video_id 字段。

    参数:
    - video_id: 视频 ID（hashDocID）
    - count: 获取评论数量上限（默认 50）

    返回:
    - JSON 字符串，包含评论列表，每条含 author/content/likes
    """
    try:
        comments = []
        last_buffer = ""

        while len(comments) < count:
            with httpx.Client(timeout=30) as c:
                r = c.post(
                    f"{BASE_URL}/api/v1/wechat_channels/fetch_comments",
                    json={"id": video_id, "lastBuffer": last_buffer},
                    headers=_headers(),
                )
                r.raise_for_status()
                data = r.json()

            batch = (data.get("data") or {}).get("comments", [])
            if not batch:
                break
            for cmt in batch:
                comments.append({
                    "author": cmt.get("nickName", ""),
                    "content": cmt.get("content", {}).get("str", ""),
                    "likes": cmt.get("likeNum", 0),
                })
            last_buffer = (data.get("data") or {}).get("lastBuffer", "")
            if not last_buffer:
                break

        return json.dumps({
            "video_id": video_id,
            "count": len(comments),
            "comments": comments[:count],
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
