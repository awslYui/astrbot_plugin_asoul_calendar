import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 日程", "1.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        # 修复位置参数
        try:
            self.context.register_task("0 */12 * * *", self.update_calendar_image, "A-SOUL 日程更新")
        except:
            pass

    def parse_summary_v2(self, summary_text):
        types = ["突击", "2D", "日常", "节目"]
        found_tag = "日常"
        
        # 匹配 B站官方格式: 【类型】成员名: 【类型】标题
        match = re.search(r"^【(.*?)】(.*?)[:：]", summary_text)
        if match:
            tag_part = match.group(1)
            name_part = match.group(2)
            title_part = summary_text.split("】", 2)[-1].strip() if "】" in summary_text else summary_text
            for t in types:
                if t in tag_part: found_tag = t
            return found_tag, name_part.replace("突击", "").strip(), title_part
        return found_tag, "团播/夜谈", summary_text

    def parse_ics_advanced(self, text):
        events = []
        items = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", text, re.S)
        for item in items:
            summary = re.search(r"SUMMARY:(.*)", item).group(1).strip() if re.search(r"SUMMARY:(.*)", item) else ""
            dtstart = re.search(r"DTSTART:(.*)", item).group(1).strip() if re.search(r"DTSTART:(.*)", item) else ""
            location = re.search(r"LOCATION:(.*)", item).group(1).strip() if re.search(r"LOCATION:(.*)", item) else ""
            if not dtstart or not summary: continue
            
            is_canceled = any(kw in summary for kw in ["取消", "延期", "更改"])
            tag, name, title = self.parse_summary_v2(summary)
            
            try:
                t_str = dtstart[:16].replace('Z','')
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events.append({"time": bj_dt, "tag": tag, "name": name, "title": title, "url": location, "canceled": is_canceled})
            except: continue
        return sorted(events, key=lambda x: x["time"])

    def get_color(self, url, title):
        mapping = {"22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", "30849777": "#C93773", "30858592": "#7252C0"}
        for uid, color in mapping.items():
            if uid in url: return color
        kws = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in kws.items():
            if k in title: return v
        return "#5C6370"

    def draw_event_card(self, draw, x, y, ev, fonts):
        COL_W, CARD_H = 280, 105
        # 左侧灰色线
        draw.line([x - 18, y + 10, x - 18, y + 150], fill="#DCDFE6", width=2)
        # 时间
        draw.text((x, y), ev["time"].strftime('%H:%M'), fill="#99A2AA", font=fonts['time'])
        
        y_c = y + 35
        m_clr = "#E0E0E0" if ev["canceled"] else self.get_color(ev["url"], ev["name"])
        draw.rounded_rectangle([x, y_c, x + COL_W - 45, y_c + CARD_H], radius=15, fill=m_clr)
        
        # Tag 标签
        tx, ty = x + 15, y_c + 15
        draw.rounded_rectangle([tx, ty, tx + 65, ty + 28], radius=8, fill="#FFFFFF44")
        draw.text((tx + 12, ty + 3), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        
        # 名字
        draw.text((tx + 80, ty + 1), ev["name"], fill="#FFFFFF", font=fonts['name'])
        
        # 标题 (自动截断)
        title = ev["title"]
        d_title = title if len(title) <= 12 else title[:11] + "..."
        draw.text((x + 15, ty + 42), d_title, fill="#FFFFFF", font=fonts['title'])
        
        if ev["canceled"]:
            draw.line([x + 10, y_c + CARD_H//2, x + COL_W - 55, y_c + CARD_H//2], fill="#444444", width=3)
        return 170

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                all_ev = self.parse_ics_advanced(resp.text)
            except: return False
            
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_w = today - timedelta(days=today.weekday())
            w_data = {i: [] for i in range(7)}
            for e in all_ev:
                diff = (e["time"].date() - start_w.date()).days
                if 0 <= diff <= 6: w_data[diff].append(e)

            # 画布
            CW, MT = 300, 160
            max_c = max([len(v) for v in w_data.values()] + [1])
            img = PILImage.new('RGB', (CW * 7 + 100, MT + max_c * 180 + 100), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                fonts = {
                    'title': ImageFont.truetype(self.font_path, 40),
                    'date': ImageFont.truetype(self.font_path, 22),
                    'time': ImageFont.truetype(self.font_path, 20),
                    'tag': ImageFont.truetype(self.font_path, 17),
                    'name': ImageFont.truetype(self.font_path, 21),
                    'title': ImageFont.truetype(self.font_path, 19)
                }
            except: return False

            draw.text((CW*3.5-60, 45), "本周日程", fill="#222222", font=fonts['title'])
            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            
            for i in range(7):
                x = 65 + i * CW
                curr_d = start_w + timedelta(days=i)
                d_clr = "#00AEEC" if i == datetime.now().weekday() else "#666666"
                draw.text((x, 105), curr_d.strftime('%m/%d') + f" {w_names[i]}", fill=d_clr, font=fonts['date'])
                
                y_o = MT
                for ev in w_data[i]:
                    y_o += self.draw_event_card(draw, x, y_o, ev, fonts)
            
            os.makedirs("data", exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        if os.path.exists(self.image_path): yield event.image_result(self.image_path)
        else: yield event.plain_result("请执行 /更新日程表")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在高精同步 A-SOUL 日程...")
        if await self.update_calendar_image(): yield event.image_result(self.image_path)
        else: yield event.plain_result("同步失败，请检查字体文件。")
