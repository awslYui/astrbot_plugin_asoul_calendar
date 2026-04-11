import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 直播日程", "1.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        # 使用关键字参数确保兼容性
        self.context.register_task(
            cron="0 */12 * * *", 
            func=self.update_calendar_image, 
            desc="A-SOUL 日程表定时更新"
        )

    def parse_ics_advanced(self, text):
        events = []
        items = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", text, re.S)
        for item in items:
            summary = re.search(r"SUMMARY:(.*)", item).group(1).strip() if re.search(r"SUMMARY:(.*)", item) else "未知直播"
            dtstart = re.search(r"DTSTART:(.*)", item).group(1).strip() if re.search(r"DTSTART:(.*)", item) else ""
            location = re.search(r"LOCATION:(.*)", item).group(1).strip() if re.search(r"LOCATION:(.*)", item) else ""
            
            if not dtstart: continue
            is_canceled = any(kw in summary for kw in ["取消", "延期", "更改"])
            
            try:
                t_str = dtstart[:16].replace('Z','')
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events.append({"time": bj_dt, "title": summary, "url": location, "canceled": is_canceled})
            except: continue
        return sorted(events, key=lambda x: x["time"])

    def get_color_by_url(self, url, title):
        mapping = {"22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", "30849777": "#C93773", "30858592": "#7252C0"}
        for uid, color in mapping.items():
            if uid in url: return color
        kws = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in kws.items():
            if k in title: return v
        return "#7A869A" # 团播或默认色

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                all_events = self.parse_ics_advanced(resp.text)
            except: return False
            
            # 时间轴计算
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_of_week = today - timedelta(days=today.weekday())
            week_data = {i: [] for i in range(7)}
            for ev in all_events:
                diff = (ev["time"].date() - start_of_week.date()).days
                if 0 <= diff <= 6: week_data[diff].append(ev)

            # 画布参数
            COL_W, MARGIN_T, CARD_H = 240, 140, 90
            max_ev = max([len(v) for v in week_data.values()] + [1])
            img_h = MARGIN_T + max_ev * 160 + 100
            img = PILImage.new('RGB', (COL_W * 7 + 100, img_h), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                f_t = ImageFont.truetype(self.font_path, 38)
                f_d = ImageFont.truetype(self.font_path, 20)
                f_m = ImageFont.truetype(self.font_path, 18)
            except: return False

            draw.text((40, 40), "本周日程", fill="#222222", font=f_t)
            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            
            for i in range(7):
                x = 50 + i * COL_W
                curr_d = start_of_week + timedelta(days=i)
                # 日期头
                draw.text((x, 100), curr_d.strftime('%m/%d') + f" {w_names[i]}", fill="#666666", font=f_d)
                # 高亮今天
                if i == datetime.now().weekday():
                    draw.rounded_rectangle([x-5, 95, x+COL_W-25, 130], radius=5, outline="#E799B0", width=2)
                
                y = MARGIN_T
                for ev in week_data[i]:
                    draw.text((x, y), ev["time"].strftime('%H:%M'), fill="#666666", font=f_d)
                    y += 30
                    m_clr = "#D0D3D9" if ev["canceled"] else self.get_color_by_url(ev["url"], ev["title"])
                    draw.rounded_rectangle([x, y, x + COL_W - 35, y + CARD_H], radius=12, fill=m_clr)
                    
                    # 标题文字换行处理
                    txt = ev["title"]
                    display_txt = txt if len(txt) <= 10 else txt[:9] + "..."
                    draw.text((x + 12, y + 25), display_txt, fill="#FFFFFF", font=f_m)
                    
                    if ev["canceled"]:
                        draw.line([x + 10, y + 45, x + COL_W - 45, y + 45], fill="#444444", width=2)
                    y += CARD_H + 40
            
            os.makedirs("data", exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        if os.path.exists(self.image_path): yield event.image_result(self.image_path)
        else: yield event.plain_result("暂无图片，请执行 /更新日程表")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在同步日程并生成 B 站风格日历...")
        if await self.update_calendar_image(): yield event.image_result(self.image_path)
        else: yield event.plain_result("生成失败，请检查仓库字体文件。")
