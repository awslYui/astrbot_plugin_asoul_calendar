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
        
        try:
            self.context.register_task("0 */12 * * *", self.update_calendar_image)
        except:
            pass

    def parse_summary_v3(self, text):
        types = ["突击", "2D", "日常", "节目"]
        found_tag, found_name, found_title = "日常", "团播/夜谈", text
        match = re.search(r"^【(.*?)】(.*?)[:：]\s*(.*)", text)
        if match:
            raw_tag, name_part, found_title = match.group(1), match.group(2), match.group(3)
            for t in types:
                if t in raw_tag: found_tag = t
            found_name = name_part.replace("突击", "").replace("日常", "").strip()
        return found_tag, found_name, found_title

    def parse_ics_advanced(self, text):
        events = []
        items = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", text, re.S)
        for item in items:
            summary = re.search(r"SUMMARY:(.*)", item).group(1).strip() if re.search(r"SUMMARY:(.*)", item) else ""
            dtstart = re.search(r"DTSTART:(.*)", item).group(1).strip() if re.search(r"DTSTART:(.*)", item) else ""
            location = re.search(r"URL:(.*)", item).group(1).strip() if re.search(r"URL:(.*)", item) else ""
            if not dtstart: continue
            
            tag, name, title = self.parse_summary_v3(summary)
            try:
                t_str = dtstart[:16].replace('Z','')
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events.append({
                    "time": bj_dt, "tag": tag, "name": name, 
                    "title": title, "url": location, "canceled": False
                })
            except: continue
        
        sorted_ev = sorted(events, key=lambda x: x["time"])
        for i in range(len(sorted_ev)):
            for j in range(i + 1, len(sorted_ev)):
                if sorted_ev[i]["title"] == sorted_ev[j]["title"] and \
                   sorted_ev[i]["time"].date() == sorted_ev[j]["time"].date():
                    sorted_ev[i]["canceled"] = True
                    
        return sorted_ev

    def get_color(self, url, name):
        mapping = {
            "22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", 
            "30849777": "#C93773", "30858592": "#7252C0"
        }
        for uid, color in mapping.items():
            if uid in url: return color
        return "#5C6370"

    def draw_card(self, draw, base_img, x, y, ev, fonts):
        COL_W = 240 # 缩小卡片宽度
        draw.line([x - 15, y + 10, x - 15, y + 180], fill="#DCDFE6", width=2)
        draw.text((x, y), ev["time"].strftime('%H:%M'), fill="#99A2AA", font=fonts['time'])
        
        title = ev["title"]
        if len(title) > 33: title = title[:32] + "..."
        lines = [title[i:i+9] for i in range(0, len(title), 9)][:3] # 缩宽后每行字数减少
        
        card_h = 85 + (len(lines) - 1) * 25
        y_c = y + 35
        m_clr = "#E0E0E0" if ev["canceled"] else self.get_color(ev["url"], ev["name"])
        draw.rounded_rectangle([x, y_c, x + COL_W - 30, y_c + card_h], radius=15, fill=m_clr)
        
        tag_canvas = PILImage.new('RGBA', base_img.size, (255, 255, 255, 0))
        tag_draw = ImageDraw.Draw(tag_canvas)
        tag_draw.rounded_rectangle([x+10, y_c+15, x+65, y_c+43], radius=8, fill=(255, 255, 255, 60))
        base_img.paste(tag_canvas, (0, 0), tag_canvas)
        
        draw.text((x + 18, y_c + 18), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        draw.text((x + 75, y_c + 16), ev["name"], fill="#FFFFFF", font=fonts['name'])
        
        for i, line in enumerate(lines):
            draw.text((x + 10, y_c + 50 + i * 25), line, fill="#FFFFFF", font=fonts['title'])
            
        if ev["canceled"]:
            line_y_mid = y_c + card_h // 2
            draw.line([x + 10, line_y_mid, x + COL_W - 40, line_y_mid], fill="#444444", width=3)
        return card_h + 55 # 紧凑型间距

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

            # --- 动态高度计算核心逻辑 ---
            CW, MT = 260, 180 # 减小列宽
            day_heights = []
            for i in range(7):
                day_y = 0
                for ev in w_data[i]:
                    title = ev["title"]
                    lines_count = len([title[k:k+9] for k in range(0, len(title), 9)][:3])
                    day_y += (85 + (lines_count - 1) * 25 + 55)
                day_heights.append(day_y)
            
            actual_max_h = max(day_heights) if day_heights else 100
            img_h = MT + actual_max_h + 60 # 动态画布高度
            
            img = PILImage.new('RGB', (CW * 7 + 80, int(img_h)), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                fonts = {
                    'header': ImageFont.truetype(self.font_path, 48),
                    'date': ImageFont.truetype(self.font_path, 22),
                    'time': ImageFont.truetype(self.font_path, 19),
                    'tag': ImageFont.truetype(self.font_path, 17),
                    'name': ImageFont.truetype(self.font_path, 20),
                    'title': ImageFont.truetype(self.font_path, 19)
                }
            except: return False

            draw.text((CW*3.5-80, 50), "本 周 日 程", fill="#222222", font=fonts['header'])
            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            for i in range(7):
                x = 55 + i * CW
                curr_d = start_w + timedelta(days=i)
                d_clr = "#00AEEC" if i == datetime.now().weekday() else "#666666"
                draw.text((x, 120), curr_d.strftime('%m/%d'), fill=d_clr, font=fonts['date'])
                draw.text((x + 85, 120), w_names[i], fill=d_clr, font=fonts['date'])
                y_o = MT
                for ev in w_data[i]:
                    y_o += self.draw_card(draw, img, x, y_o, ev, fonts)
            
            os.makedirs("data", exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        if os.path.exists(self.image_path): yield event.image_result(self.image_path)
        else: yield event.plain_result("请执行 /更新日程表")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在更新日程表...")
        if await self.update_calendar_image(): yield event.image_result(self.image_path)
        else: yield event.plain_result("同步失败。")
