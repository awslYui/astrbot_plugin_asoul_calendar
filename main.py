import httpx
import json
import os
import re
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *


@register("zhijiang_calendar", "awslYui", "枝江日程表", "2.0")
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

        # 每6小时自动更新缓存
        try:
            self.context.register_task("0 */6 * * *", self._auto_update)
        except Exception as e:
            print(f"[zhijiang_calendar] 自动任务注册失败: {e}")

    # ==================== 自动更新 ====================

    async def _auto_update(self):
        """定时任务：拉取最新 ICS 数据并缓存"""
        await self._fetch_and_cache()

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

    async def _fetch_and_cache(self) -> list | None:
        """从网络获取 ICS 并缓存到本地 JSON"""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=15)
                events = self._parse_ics(resp.text)
                with open(self.cache_path, 'w', encoding='utf-8') as f:
                    json.dump(events, f, ensure_ascii=False, indent=2)
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

    def _draw_card(self, draw, base_img, x, y, ev, fonts) -> int:
        """在 (x, y) 处绘制单个日程卡片，返回卡片高度"""
        COL_W = 260
        main_color = self._get_color(ev.get("url", ""))

        # 左侧强调色条
        draw.rectangle([x - 10, y + 35, x - 6, y + 150], fill=main_color)

        # 时间
        time_str = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").strftime('%H:%M')
        draw.text((x, y), time_str, fill=main_color, font=fonts['time'])

        # 标题截断与换行（每行最多8个字符，最多3行）
        title = ev["title"]
        if len(title) > 28:
            title = title[:27] + "…"
        lines = [title[i:i+8] for i in range(0, len(title), 8)][:3]

        card_h = 100 + (len(lines) - 1) * 25
        y_c = y + 35

        # 半透明白色卡片背景
        card_overlay = PILImage.new('RGBA', (COL_W - 30, card_h), (255, 255, 255, 180))
        base_img.paste(card_overlay, (x, y_c), card_overlay)

        # 标签
        draw.rounded_rectangle([x + 10, y_c + 15, x + 65, y_c + 40], radius=5, fill=main_color)
        draw.text((x + 16, y_c + 17), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        draw.text((x + 75, y_c + 16), ev["name"], fill=main_color, font=fonts['tag'])

        # 标题多行
        for i, line in enumerate(lines):
            draw.text((x + 12, y_c + 55 + i * 28), line, fill="#555555", font=fonts['title'])

        return card_h + 60

    async def _render_weekly_image(self) -> str | None:
        """渲染本周日程图片，返回图片路径"""
        events = await self._get_events()
        if not events:
            return None

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today - timedelta(days=today.weekday())

        # 按星期几分组
        week_data = {i: [] for i in range(7)}
        for ev in events:
            ev_dt = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
            day_diff = (ev_dt.date() - week_start.date()).days
            if 0 <= day_diff <= 6:
                week_data[day_diff].append(ev)

        COL_W = 280
        MARGIN_TOP = 200
        img_w = COL_W * 7 + 100
        img = PILImage.new('RGB', (img_w, 1200), color="#FFF5F7")
        draw = ImageDraw.Draw(img)

        fonts = {
            'header': ImageFont.truetype(self.font_path, 60),
            'date':   ImageFont.truetype(self.font_path, 24),
            'time':   ImageFont.truetype(self.font_path, 22),
            'tag':    ImageFont.truetype(self.font_path, 18),
            'title':  ImageFont.truetype(self.font_path, 22),
        }

        draw.text((50, 60), "枝江 · 本周日程", fill="#E799B0", font=fonts['header'])

        day_names = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        max_y = 0

        for i in range(7):
            x = 60 + i * COL_W
            curr_day = week_start + timedelta(days=i)
            is_today = curr_day.date() == today.date()
            clr = "#E799B0" if is_today else "#888888"

            draw.text((x, 170), day_names[i], fill=clr, font=fonts['date'])
            draw.text((x + 65, 170), curr_day.strftime('%m.%d'), fill=clr, font=fonts['date'])

            y_o = MARGIN_TOP + 40
            for ev in week_data[i]:
                y_o += self._draw_card(draw, img, x, y_o, ev, fonts)
            max_y = max(max_y, y_o)

        final_img = img.crop((0, 0, img_w, max(max_y + 100, 600)))
        image_path = os.path.join(self.data_dir, "schedule_weekly.png")
        final_img.save(image_path)
        return image_path

    # ==================== 指令 ====================

    @filter.command("日程表")
    async def cmd_weekly(self, event: AstrMessageEvent):
        """发送本周枝江直播日程表图片"""
        path = await self._render_weekly_image()
        if path:
            yield event.image_result(path)
        else:
            yield event.plain_result("获取日程失败，请稍后重试。")
