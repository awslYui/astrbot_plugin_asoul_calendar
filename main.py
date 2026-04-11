import httpx
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 直播日程, "1.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        self.font_path = "MiSans-Regular.ttf" # 确保仓库里有这个字体
        
        @self.context.register_task("0 */12 * * *")
        async def scheduled_update():
            await self.update_calendar_image()

    def parse_ics_simple(self, text):
        """手动解析 ICS 文本，避开第三方库 Bug"""
        events = []
        # 使用正则匹配事件块
        items = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", text, re.S)
        for item in items:
            summary = re.search(r"SUMMARY:(.*)", item)
            dtstart = re.search(r"DTSTART:(.*)", item)
            if summary and dtstart:
                title = summary.group(1).strip()
                time_str = dtstart.group(1).strip()
                # 格式通常是 20260411T120000Z
                try:
                    utc_dt = datetime.strptime(time_str, "%Y%m%dT%H%M%SZ")
                    bj_dt = utc_dt + timedelta(hours=8) # 转换为北京时间
                    events.append((bj_dt, title))
                except:
                    continue
        return sorted(events, key=lambda x: x[0])

    def get_member_color(self, name):
        colors = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in colors.items():
            if k in name: return v
        return "#555555"

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url)
                all_events = self.parse_ics_simple(resp.text)
            except:
                return False
            
            now = datetime.now()
            upcoming = [e for e in all_events if now <= e[0] <= now + timedelta(days=7)]
            if not upcoming: upcoming = [(now, "本周暂无公开日程")]

            # 绘图逻辑 (保持之前的 B 站风格)
            card_h, pad, margin_t = 90, 20, 100
            img_h = margin_t + len(upcoming) * (card_h + pad) + 50
            img = Image.new('RGB', (780, img_h), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                font_title = ImageFont.truetype(self.font_path, 36)
                font_main = ImageFont.truetype(self.font_path, 28)
                font_time = ImageFont.truetype(self.font_path, 24)
            except:
                return False # 字体缺失也会导致失败

            draw.text((40, 30), "A-SOUL 本周直播日程", fill="#222222", font=font_title)
            
            y = margin_t
            for bj_dt, title in upcoming:
                color = self.get_member_color(title)
                draw.rounded_rectangle([40, y, 740, y + card_h], radius=15, fill=color)
                draw.text((65, y + 30), bj_dt.strftime('%m月%d日 %H:%M'), fill="#FFFFFF", font=font_time)
                draw.text((260, y + 25), f"|  {title}", fill="#FFFFFF", font=font_main)
                y += card_h + pad
            
            os.makedirs("data", exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        if os.path.exists(self.image_path):
            yield event.image_result(self.image_path)
        else:
            yield event.plain_result("暂无日程图片，请执行 /更新日程表")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在同步日历...")
        if await self.update_calendar_image():
            yield event.image_result(self.image_path)
        else:
            yield event.plain_result("更新失败，请检查字体文件或网络。")
