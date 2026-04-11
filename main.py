import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 直播日程 (B站复刻版)", "1.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        # 修正：位置参数写法
        try:
            self.context.register_task("0 */12 * * *", self.update_calendar_image, "A-SOUL 日程更新")
        except:
            pass

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

    def get_color(self, url, title):
        # 团播/夜谈常用深色
        group_color = "#5C6370" 
        mapping = {"22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", "30849777": "#C93773", "30858592": "#7252C0"}
        found = False
        for uid, color in mapping.items():
            if uid in url: return color
        kws = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in kws.items():
            if k in title: return v
        return group_color

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                all_events = self.parse_ics_advanced(resp.text)
            except: return False
            
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_of_week = today - timedelta(days=today.weekday())
            week_data = {i: [] for i in range(7)}
            for ev in all_events:
                diff = (ev["time"].date() - start_of_week.date()).days
                if 0 <= diff <= 6: week_data[diff].append(ev)

            # 画布参数：复刻例图比例
            COL_W, MARGIN_T, CARD_H = 260, 150, 85
            max_ev = max([len(v) for v in week_data.values()] + [1])
            img_h = MARGIN_T + max_ev * 140 + 100
            img = PILImage.new('RGB', (COL_W * 7 + 80, img_h), color="#FFFFFF") # 改为纯白底
            draw = ImageDraw.Draw(img)
            
            try:
                f_t = ImageFont.truetype(self.font_path, 42)
                f_d = ImageFont.truetype(self.font_path, 22)
                f_m = ImageFont.truetype(self.font_path, 19)
            except: return False

            draw.text((COL_W*3.5-40, 40), "本 周 日 程", fill="#222222", font=f_t)
            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            
            for i in range(7):
                x = 40 + i * COL_W
                curr_d = start_of_week + timedelta(days=i)
                # 绘制日期头
                is_today = (i == datetime.now().weekday())
                d_color = "#00AEEC" if is_today else "#666666"
                draw.text((x, 100), curr_d.strftime('%Y/%m/%d'), fill=d_color, font=f_d)
                draw.text((x + 130, 100), w_names[i], fill=d_color, font=f_d)
                
                y = MARGIN_T
                for ev in week_data[i]:
                    # 时间文字
                    draw.text((x, y), ev["time"].strftime('%H:%M'), fill="#99a2aa", font=f_m)
                    y += 30
                    # 卡片颜色
                    m_clr = "#E0E0E0" if ev["canceled"] else self.get_color(ev["url"], ev["title"])
                    draw.rounded_rectangle([x, y, x + COL_W - 40, y + CARD_H], radius=10, fill=m_clr)
                    
                    # 标题（支持两行简易处理）
                    title = ev["title"]
                    if len(title) > 10:
                        draw.text((x + 12, y + 15), title[:10], fill="#FFFFFF", font=f_m)
                        draw.text((x + 12, y + 42), title[10:20], fill="#FFFFFF", font=f_m)
                    else:
                        draw.text((x + 12, y + 30), title, fill="#FFFFFF", font=f_m)
                    
                    if ev["canceled"]:
                        draw.line([x + 10, y + CARD_H//2, x + COL_W - 50, y + CARD_H//2], fill="#555555", width=3)
                    y += CARD_H + 35
            
            os.makedirs("data", exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        if os.path.exists(self.image_path): yield event.image_result(self.image_path)
        else: yield event.plain_result("请执行 /更新日程表")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在同步 A-SOUL 日程...")
        if await self.update_calendar_image(): yield event.image_result(self.image_path)
        else: yield event.plain_result("同步失败，请检查字体文件。")
