import asyncio
import contextlib
import httpx
import json
import os
import re
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *


@register("zhijiang_calendar", "awslYui", "枝江日程表", "2.3.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.data_dir = "data/zhijiang_calendar"
        os.makedirs(self.data_dir, exist_ok=True)
        self.cache_path = os.path.join(self.data_dir, "events_cache.json")

        # 字体路径：优先使用插件目录下的 msyh.ttf
        font_candidates = [
            os.path.join(os.path.dirname(__file__), "msyh.ttf"),
            os.path.join(os.path.dirname(__file__), "msyh.ttc"),
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyhbd.ttc",
        ]
        self.font_path = None
        for fp in font_candidates:
            if os.path.exists(fp):
                self.font_path = fp
                break
        if self.font_path is None:
            raise FileNotFoundError(
                "未找到中文字体文件。请将 msyh.ttf 放置于插件目录下。\n"
                "已搜索路径: " + ", ".join(font_candidates)
            )

        self._fetch_lock = asyncio.Lock()
        self._update_task = None

    async def initialize(self):
        """启动缓存更新循环。"""
        self._update_task = asyncio.create_task(self._update_loop())

    async def terminate(self):
        """插件卸载时停止后台任务。"""
        if self._update_task:
            self._update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._update_task

    # ==================== 自动更新 ====================

    async def _update_loop(self):
        """启动后立即更新，之后每 6 小时更新一次。"""
        while True:
            await self._fetch_and_cache()
            await asyncio.sleep(6 * 60 * 60)

    # ==================== ICS 解析 ====================

    @staticmethod
    def _parse_summary(text: str) -> tuple:
        """解析 SUMMARY 字段，返回 (标签, 成员名, 标题)"""
        types = ["突击", "2D", "日常", "节目"]
        found_tag, found_name, found_title = "日常", "", text.strip()

        match = re.search(r"^【(.*?)】(.*?)[:：]\s*(.*)", text)
        if match:
            raw_tag = match.group(1)
            name_part = match.group(2)
            found_title = match.group(3)

            for t in types:
                if t in raw_tag:
                    found_tag = t
            found_name = name_part.replace("突击", "").replace("日常", "").strip()

        return found_tag, found_name, found_title

    @staticmethod
    def _get_color(url: str) -> str:
        """根据直播间 URL 返回成员主题色"""
        mapping = {
            "22637261": "#E799B0",   # 嘉然
            "22625027": "#576690",   # 乃琳
            "22632424": "#DB7D74",   # 贝拉
            "30849777": "#C93773",   # 心宜
            "30858592": "#7252C0",   # 思诺
        }
        for room_id, color in mapping.items():
            if room_id in url:
                return color
        return "#E799B0"

    def _parse_ics(self, text: str) -> list:
        """解析 ICS 文本，返回按时间排序的事件列表"""
        events = {}
        text = re.sub(r'\r?\n\s', '', text)
        blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)

        for block in blocks:
            summary = re.search(r"^SUMMARY:(.*?)$", block, re.M)
            uid = re.search(r"^UID:(.*?)$", block, re.M)
            dtstart = re.search(r"^DTSTART:(.*?)$", block, re.M)
            url_field = re.search(r"^URL:(.*?)$", block, re.M)

            if not summary or not uid or not dtstart:
                continue

            sum_text = summary.group(1).strip()
            tag, name, title = self._parse_summary(sum_text)

            t_start = dtstart.group(1).strip()
            actual_url = url_field.group(1).strip() if url_field else ""
            u_id = uid.group(1).strip()

            try:
                t_str = t_start.replace('Z', '')[:15]
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events[u_id] = {
                    "uid": u_id,
                    "time": bj_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "tag": tag,
                    "name": name,
                    "title": title,
                    "url": actual_url,
                }
            except ValueError:
                continue

        return sorted(events.values(), key=lambda x: x["time"])

    # ==================== 网络与缓存 ====================

    @staticmethod
    def _now_bj() -> datetime:
        """返回无时区标记的北京时间，和缓存中的时间格式保持一致。"""
        return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    def _merge_with_cache(self, fresh_events: list) -> list:
        """合并新日程，并保留本周已经结束、被上游移除的历史日程。"""
        cached_events = self._load_cache() or []
        now = self._now_bj()
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # 新数据是未来日程的权威来源；同 UID 的缓存会被新数据覆盖。
        merged = {ev["uid"]: ev for ev in fresh_events if ev.get("uid")}
        for ev in cached_events:
            uid = ev.get("uid")
            try:
                event_time = datetime.strptime(
                    ev["time"], "%Y-%m-%d %H:%M:%S"
                )
            except (KeyError, TypeError, ValueError):
                continue

            if uid and week_start <= event_time < now and uid not in merged:
                merged[uid] = ev

        return sorted(merged.values(), key=lambda item: item["time"])

    async def _fetch_and_cache(self) -> list | None:
        """从网络获取 ICS，与本周历史缓存合并后原子写入本地 JSON。"""
        async with self._fetch_lock:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                try:
                    resp = await client.get(self.url, timeout=15)
                    resp.raise_for_status()
                    events = self._merge_with_cache(self._parse_ics(resp.text))

                    temp_path = self.cache_path + ".tmp"
                    with open(temp_path, "w", encoding="utf-8") as f:
                        json.dump(events, f, ensure_ascii=False, indent=2)
                    os.replace(temp_path, self.cache_path)
                    return events
                except Exception as e:
                    print(f"[zhijiang_calendar] 获取日程失败: {e}")
                    return None

    def _load_cache(self) -> list | None:
        """从本地 JSON 加载已缓存的事件"""
        if not os.path.exists(self.cache_path):
            return None
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    async def _get_events(self) -> list | None:
        """获取事件列表（优先网络，失败则用缓存）"""
        events = await self._fetch_and_cache()
        if events is None:
            events = self._load_cache()
        return events

    # ==================== 绘图 ====================

    @staticmethod
    def _wrap_text(text: str, max_chars: int, max_lines: int) -> list[str]:
        """按字符数换行；超出最大行数时在末尾显示省略号。"""
        text = (text or "").strip()
        if not text:
            return ["未命名直播"]

        lines = [
            text[i:i + max_chars]
            for i in range(0, len(text), max_chars)
        ]
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][:-1] + "…"
        return lines

    @staticmethod
    def _save_png(image: PILImage.Image, path: str) -> str:
        """原子保存 PNG，避免发送时读到未写完的图片。"""
        temp_path = path + ".tmp"
        image.save(temp_path, format="PNG")
        os.replace(temp_path, path)
        return path

    def _load_background(self, size: tuple[int, int]) -> PILImage.Image:
        """加载用户提供的原图背景；仅按尺寸裁切缩放，不做模糊处理。"""
        background_path = os.path.join(
            os.path.dirname(__file__), "calendar_background.png"
        )
        try:
            background = PILImage.open(background_path).convert("RGBA")
        except OSError:
            return PILImage.new("RGBA", size, "#FFF5F7")

        scale = max(size[0] / background.width, size[1] / background.height)
        resized = background.resize(
            (
                max(1, round(background.width * scale)),
                max(1, round(background.height * scale)),
            ),
            PILImage.Resampling.LANCZOS,
        )
        left = max(0, (resized.width - size[0]) // 2)
        top = max(0, (resized.height - size[1]) // 2)
        return resized.crop((left, top, left + size[0], top + size[1]))

    def _render_today_image(self, events: list, today: datetime) -> str:
        """渲染适合 QQ 聊天气泡直接阅读的今日大字版。"""
        today_events = []
        for ev in events:
            try:
                ev_dt = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
            except (KeyError, TypeError, ValueError):
                continue
            if ev_dt.date() == today.date():
                today_events.append(ev)

        img_w = 1080
        margin = 54
        gap = 22
        header_h = 500
        fonts = {
            "header": ImageFont.truetype(self.font_path, 58),
            "date": ImageFont.truetype(self.font_path, 30),
            "time": ImageFont.truetype(self.font_path, 44),
            "tag": ImageFont.truetype(self.font_path, 24),
            "name": ImageFont.truetype(self.font_path, 32),
            "title": ImageFont.truetype(self.font_path, 34),
            "empty": ImageFont.truetype(self.font_path, 36),
            "footer": ImageFont.truetype(self.font_path, 22),
        }

        card_specs = []
        for ev in today_events:
            lines = self._wrap_text(ev.get("title", ""), 22, 3)
            card_specs.append((ev, lines, 150 + len(lines) * 44))

        content_h = sum(spec[2] for spec in card_specs)
        content_h += max(0, len(card_specs) - 1) * gap
        img_h = max(560, header_h + content_h + 110)

        img = self._load_background((img_w, img_h))
        draw = ImageDraw.Draw(img)
        weekdays = "一二三四五六日"

        # 为标题区铺一层半透明底，背景仍保持原图清晰。
        draw.rounded_rectangle(
            [margin - 18, 30, margin + 420, 190],
            radius=24, fill=(255, 255, 255, 224),
        )
        draw.rounded_rectangle(
            [img_w - 270, 132, img_w - 42, 190],
            radius=18, fill=(255, 255, 255, 214),
        )
        draw.text((margin, 48), "今日直播", fill="#B92761", font=fonts["header"])
        date_text = (
            f"{today.month}月{today.day}日  "
            f"星期{weekdays[today.weekday()]}"
        )
        draw.text((margin, 126), date_text, fill="#3F4147", font=fonts["date"])
        draw.text(
            (img_w - 248, 148),
            f"更新于 {self._now_bj():%H:%M}",
            fill="#6D6E75",
            font=fonts["footer"],
        )

        if not card_specs:
            box = [margin, header_h, img_w - margin, img_h - 70]
            draw.rounded_rectangle(
                box, radius=28, fill="#FFFFFF", outline="#F0D7DF", width=2
            )
            message = "今天暂无直播安排"
            bbox = draw.textbbox((0, 0), message, font=fonts["empty"])
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            draw.text(
                ((img_w - text_w) / 2,
                 header_h + (box[3] - header_h - text_h) / 2),
                message,
                fill="#AAAAAA",
                font=fonts["empty"],
            )
        else:
            y = header_h
            now = self._now_bj()
            for ev, lines, card_h in card_specs:
                color = self._get_color(ev.get("url", ""))
                ev_dt = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
                ended = ev_dt < now
                card_fill = "#FAFAFA" if ended else "#FFFFFF"
                draw.rounded_rectangle(
                    [margin, y, img_w - margin, y + card_h],
                    radius=26,
                    fill=(248, 248, 248, 248) if ended else (255, 255, 255, 246),
                    outline="#EFDCE2",
                    width=2,
                )
                draw.rounded_rectangle(
                    [margin, y, margin + 12, y + card_h],
                    radius=6,
                    fill=color,
                )

                time_color = "#777B84" if ended else "#8F3754"
                draw.text(
                    (margin + 36, y + 34),
                    ev_dt.strftime("%H:%M"),
                    fill=time_color,
                    font=fonts["time"],
                )
                if ended:
                    draw.text(
                        (margin + 38, y + 91),
                        "已结束",
                        fill="#AAAAAA",
                        font=fonts["footer"],
                    )

                tag = ev.get("tag") or "日常"
                tag_box = [margin + 210, y + 34, margin + 306, y + 72]
                draw.rounded_rectangle(tag_box, radius=10, fill=color)
                draw.text(
                    (tag_box[0] + 15, tag_box[1] + 4),
                    tag,
                    fill="#FFFFFF",
                    font=fonts["tag"],
                )
                draw.text(
                    (margin + 330, y + 32),
                    ev.get("name") or "A-SOUL",
                    fill="#8F3754" if not ended else "#777B84",
                    font=fonts["name"],
                )
                for line_index, line in enumerate(lines):
                    draw.text(
                        (margin + 210, y + 92 + line_index * 44),
                        line,
                        fill="#444444",
                        font=fonts["title"],
                    )
                y += card_h + gap

        path = os.path.join(self.data_dir, "schedule_today.png")
        return self._save_png(img, path)

    def _render_weekly_image(
        self, events: list, today: datetime, week_start: datetime
    ) -> str:
        """渲染横向七列本周总览。"""
        week_data = {i: [] for i in range(7)}
        for ev in events:
            try:
                ev_dt = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
            except (KeyError, TypeError, ValueError):
                continue
            day_diff = (ev_dt.date() - week_start.date()).days
            if 0 <= day_diff <= 6:
                week_data[day_diff].append(ev)

        col_w = 300
        margin = 54
        header_h = 760
        panel_top = 690
        card_top = 820
        img_w = col_w * 7 + margin * 2
        fonts = {
            "header": ImageFont.truetype(self.font_path, 58),
            "range": ImageFont.truetype(self.font_path, 28),
            "day": ImageFont.truetype(self.font_path, 30),
            "time": ImageFont.truetype(self.font_path, 27),
            "meta": ImageFont.truetype(self.font_path, 20),
            "title": ImageFont.truetype(self.font_path, 26),
            "empty": ImageFont.truetype(self.font_path, 23),
            "footer": ImageFont.truetype(self.font_path, 20),
        }

        card_specs = {}
        for day_index, day_events in week_data.items():
            cards = []
            for ev in day_events:
                lines = self._wrap_text(ev.get("title", ""), 9, 3)
                card_h = 88 + len(lines) * 33
                cards.append((ev, lines, card_h))
            card_specs[day_index] = cards

        tallest_column = max(
            (
                sum(card[2] for card in cards) + max(0, len(cards) - 1) * 14
                for cards in card_specs.values()
            ),
            default=0,
        )
        img_h = max(header_h + tallest_column + 150, 1030)
        img = self._load_background((img_w, img_h))
        draw = ImageDraw.Draw(img)

        # 标题直接融入背景，用轻描边保证清晰，避免厚重的悬浮白框。
        draw.text(
            (margin, 46), "枝江 · 本周日程", fill="#B92761",
            font=fonts["header"], stroke_width=2, stroke_fill="#FFF8FA",
        )
        week_end = week_start + timedelta(days=6)
        draw.text(
            (margin, 128),
            f"{week_start:%m.%d} — {week_end:%m.%d}",
            fill="#4E5159", font=fonts["range"],
            stroke_width=1, stroke_fill="#FFF8FA",
        )
        draw.rounded_rectangle(
            [img_w - 250, 132, img_w - 42, 184],
            radius=16, fill=(255, 248, 250, 180),
        )
        draw.text(
            (img_w - 230, 146),
            f"更新于 {self._now_bj():%H:%M}",
            fill="#666A72", font=fonts["footer"],
        )

        # 七天共享一块柔和的日历底座，避免七张独立白卡与插画割裂。
        surface = [margin, panel_top, img_w - margin - 18, img_h - 50]
        draw.rounded_rectangle(
            surface, radius=30, fill=(255, 248, 250, 222),
            outline=(237, 196, 211, 224), width=2,
        )
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        now = self._now_bj()
        for day_index in range(7):
            x = margin + day_index * col_w
            curr_day = week_start + timedelta(days=day_index)
            is_today = curr_day.date() == today.date()
            if day_index:
                draw.line(
                    (x - 9, panel_top + 22, x - 9, img_h - 72),
                    fill=(235, 204, 214, 185), width=2,
                )
            if is_today:
                draw.rounded_rectangle(
                    [x + 8, panel_top + 10, x + col_w - 28, img_h - 60],
                    radius=20, fill=(255, 235, 242, 132),
                    outline="#E799B0", width=3,
                )
            draw.text(
                (x + 20, panel_top + 27),
                weekdays[day_index],
                fill="#B92761" if is_today else "#3F4147",
                font=fonts["day"],
            )
            draw.text(
                (x + 20, panel_top + 70),
                curr_day.strftime("%m.%d"),
                fill="#6D6E75",
                font=fonts["range"],
            )
            if is_today:
                draw.rounded_rectangle(
                    [x + col_w - 95, panel_top + 28,
                     x + col_w - 38, panel_top + 63],
                    radius=9,
                    fill="#E799B0",
                )
                draw.text(
                    (x + col_w - 84, panel_top + 31),
                    "今天",
                    fill="#FFFFFF",
                    font=fonts["meta"],
                )

            cards = card_specs[day_index]
            card_y = card_top
            if not cards:
                draw.text(
                    (x + 22, card_y + 50),
                    "暂无日程",
                    fill="#72757D",
                    font=fonts["empty"],
                )

            for ev, lines, card_h in cards:
                color = self._get_color(ev.get("url", ""))
                ev_dt = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
                ended = ev_dt < now
                draw.rounded_rectangle(
                    [x + 14, card_y, x + col_w - 32, card_y + card_h],
                    radius=14,
                    fill=(250, 250, 251, 232) if ended else (255, 255, 255, 236),
                )
                draw.rounded_rectangle(
                    [x + 14, card_y, x + 23, card_y + card_h],
                    radius=4,
                    fill=color,
                )
                draw.text(
                    (x + 38, card_y + 16),
                    ev_dt.strftime("%H:%M"),
                    fill="#777B84" if ended else "#8F3754",
                    font=fonts["time"],
                )

                meta = " · ".join(
                    part for part in (
                        ev.get("name") or "A-SOUL",
                        ev.get("tag") or "日常",
                    ) if part
                )
                if ended:
                    meta += " · 已结束"
                draw.text(
                    (x + 38, card_y + 52),
                    meta,
                    fill="#777B84" if ended else "#8F3754",
                    font=fonts["meta"],
                )
                for line_index, line in enumerate(lines):
                    draw.text(
                        (x + 38, card_y + 83 + line_index * 33),
                        line,
                        fill="#555555",
                        font=fonts["title"],
                    )
                card_y += card_h + 14

        path = os.path.join(self.data_dir, "schedule_weekly.png")
        return self._save_png(img, path)

    async def _render_calendar_images(self) -> tuple[str, str] | None:
        """只拉取一次数据，同时生成今日详图和本周总览。"""
        events = await self._get_events()
        if events is None:
            return None

        today = self._now_bj().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_start = today - timedelta(days=today.weekday())
        today_path = self._render_today_image(events, today)
        weekly_path = self._render_weekly_image(events, today, week_start)
        return today_path, weekly_path

    # ==================== 指令 ====================

    @filter.command("日程表")
    async def cmd_weekly(self, event: AstrMessageEvent):
        """发送本周枝江直播日程表图片"""
        paths = await self._render_calendar_images()
        if not paths:
            yield event.plain_result("获取日程失败，请稍后重试。")
            return

        today_path, weekly_path = paths
        yield event.image_result(today_path)
        yield event.image_result(weekly_path)
